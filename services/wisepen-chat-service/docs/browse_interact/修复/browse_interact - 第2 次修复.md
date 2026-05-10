先 review 这个 `ref.py`。这个版本**明显比上一版靠谱**：`snapshot_id` 已经改成 `resolve_current()`，`fill_ref` 也不再下钻找 fillable descendant，`select/check` 也有了不可操作错误分支。核心方向是对的。

但现在还有几个真实逻辑坑。

---

# 1. `select_ref / check_ref` 会误判“自身就是目标”的元素

当前：

```python
async def resolve_descendant_target(target, selector: str):
    if await target.count() == 0:
        return None

    descendant = target.locator(selector).first
    if await descendant.count() > 0:
        return descendant

    return None
```

问题：`target.locator(selector)` 只找 **后代**，不包含 `target` 自己。

所以如果 ref 本身就是：

```html
<select data-agent-ref="e3">...</select>
```

`select_ref` 会找不到 descendant，然后返回：

```text
REF_NOT_SELECTABLE
```

同理，如果 ref 本身就是：

```html
<input type="checkbox" data-agent-ref="e4">
```

`check_ref` 也可能误判为不可勾选。

这是 P0 bug。

## 修法

不要用一个通用 `resolve_descendant_target()` 解决 select/check。拆成两个明确函数：

```python
async def resolve_select_target(target):
    if await target.count() == 0:
        return None

    try:
        tag = await target.evaluate("el => el.tagName.toLowerCase()")
        disabled = await target.evaluate(
            "el => Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true'"
        )
        if tag == "select" and not disabled:
            return target
    except Exception:
        return None

    return None
```

`check` 同理：

```python
async def resolve_check_target(target):
    if await target.count() == 0:
        return None

    try:
        data = await target.evaluate(
            """el => ({
                tag: el.tagName.toLowerCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                role: (el.getAttribute('role') || '').toLowerCase(),
                disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true'
            })"""
        )

        if data["disabled"]:
            return None

        if data["tag"] == "input" and data["type"] in ("checkbox", "radio"):
            return target

        if data["role"] in ("checkbox", "radio"):
            return target

    except Exception:
        return None

    return None
```

第一版建议**不要 descendant fallback**。
snapshot 层应该暴露真实 `select / checkbox / radio`，action 层不要再猜容器里的后代。

---

# 2. `select_ref` 里还有宽容转换残留

当前：

```python
selected = await select_target.select_option(value=str(value))
```

和：

```python
selected = await select_target.select_option(label=str(label))
```

这和你定下的规范冲突：**参数类型由 schema 保证，工具层不做 `str(value)` 这种宽容转换。**

如果 schema 里 `value` / `label` 是 string，就直接用：

```python
selected = await select_target.select_option(value=value)
```

或：

```python
selected = await select_target.select_option(label=label)
```

字段缺失可以默认，字段存在就按原始类型使用，不要猜。

---

# 3. `fill_ref` 里的 `Escape` 是隐藏副作用

当前：

```python
await fill_target.fill(text)
await page.keyboard.press("Escape")
```

这个很危险。

`fill_ref` 的语义应该是：

```text
只填充目标元素。
不点击。
不关闭弹窗。
不改变页面结构。
不 invalidate snapshot。
```

但 `Escape` 可能会：

```text
关闭搜索弹窗
关闭 modal
关闭 autocomplete
关闭登录框
触发表单 UI 状态变化
```

既然 `fill_ref` 成功后不 invalidate snapshot，那它更不应该偷偷按 Escape。否则 DOM/UI 变了，但 snapshot 还被认为有效。

建议删除：

```python
await page.keyboard.press("Escape")
```

如果某些网站填完后需要关闭浮层，让 agent 显式调用 `key` action。

---

# 4. `click_ref` 仍然用手动坐标点击，建议改回 actionability 点击

当前：

```python
await element.scroll_into_view_if_needed()
box = await element.bounding_box()

if box:
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    await page.mouse.click(x, y)
else:
    await element.click()
```

这有两个问题：

```text
1. 绕开了 Playwright 的 actionability 检查。
2. 大元素中心点可能不是实际可点击区域。
```

现在 snapshot 层应该只暴露真实可操作元素，所以 click 层没必要再用坐标模拟。建议改成：

```python
target = page.locator(selector).first

if await target.count() == 0:
    ...

await target.scroll_into_view_if_needed()
await target.click()
```

这和 `fill_ref / select_ref / check_ref` 的 Locator 风格也统一。

---

# 5. `select_ref / check_ref` 成功后可以考虑 intervention 检测

`click_ref` 成功后有：

```python
intervention_error = await intervention.detect(page)
```

但 `select_ref / check_ref` 没有。

通常 select/check 不会触发登录/验证码，但某些页面可能会因为选择国家、勾选条款触发表单更新或风控。这个不是 P0，但为了行为一致，可以在 `snapshot_manager.invalidate()` 后加同样的 intervention detection。

不过如果你想保持克制，先不加也可以。

---

# 6. `processor` 参数在这个文件里完全没用

四个 handler 都收了：

```python
processor: ContentProcessor
```

但没有使用。

这应该是为了 handler 签名统一，可以接受。不要为了这个单独重构。
但如果未来你做类型检查或 lint，会有 unused 参数噪音。

---

# 当前优先级

## P0 必改

```text
1. select_ref/check_ref 必须识别 ref 自身就是 select/checkbox/radio。
2. 删除 select_ref 里的 str(value) / str(label)。
3. 删除 fill_ref 里的 Escape。
```

## P1 建议改

```text
4. click_ref 改成 Locator.click()，不要手动 mouse 坐标点击。
5. select_ref/check_ref 成功后考虑 intervention detection。
```

---

# 给 Codex 的最小提示词

```text
请只修改 browse_interact 的 ref actions，不做无关重构。

目标：
1. 修复 select_ref/check_ref 不能操作 ref 自身的问题。
   当前 resolve_descendant_target 只查后代，不包含 target 自身。
   请改成 select_ref 检查 target 自身是否为 select。
   check_ref 检查 target 自身是否为 checkbox/radio。
   第一版不要自动下钻容器后代。

2. 删除 select_ref 中的 str(value) / str(label)。
   value/label 类型由 schema 保证，直接传给 select_option。

3. 删除 fill_ref 中的 await page.keyboard.press("Escape")。
   fill_ref 不应有隐藏副作用，也不应改变页面 UI 状态。

4. 建议把 click_ref 从 ElementHandle + mouse.click 坐标点击改为 Locator.click。
   使用：
   target = page.locator(selector).first
   await target.scroll_into_view_if_needed()
   await target.click()

必须保持：
- click_ref 成功后 invalidate snapshot。
- fill_ref 成功后不 invalidate snapshot。
- select_ref/check_ref 成功后 invalidate snapshot。
- snapshot_id 使用 resolve_current。
- fill_ref 不返回原始 text，只返回 text_length。
- 错误协议不变。
- 不改 session 行为。
- 不改 schema。
```

一句话：**这个文件已经修到了正确方向，但还残留“select/check 只查后代”“select 参数 str 转换”“fill 后按 Escape”这三个实际坑。**
