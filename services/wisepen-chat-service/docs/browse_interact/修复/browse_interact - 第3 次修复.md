行，先 review 这两个文件：**`snapshot.py` 和 `snapshot_script.js`**。结论先说：整体方向比之前好多了，但现在真正的问题集中在 **snapshot 暴露策略**，它会直接误导 agent 去填 link/banner。

---

# 1. `snapshot.py` review

## 1.1 做得对的地方

`parse_snapshot_raw()` 保留了 JS 边界校验，这是合理的。`page.evaluate()` 返回值是跨语言边界，不能完全信任，所以这里校验 `raw` 是字符串、JSON 是 object、`schemaVersion` 正确、`elements` 是 list、`ref` 格式正确，这些都该保留。

`resolve_current()` 的设计也是对的：
如果 action 没传 `snapshot_id`，但当前存在有效 snapshot，就使用当前 snapshot；如果显式传了旧 snapshot，就返回 stale。这个正好解决了模型忘记回传 `snapshot_id` 的问题。

---

## 1.2 问题一：`SNAPSHOT_ID_LENGTH` 名字不准

现在是：

```python
SNAPSHOT_ID_LENGTH = 8
...
snapshot_id = secrets.token_hex(SNAPSHOT_ID_LENGTH)
```

`secrets.token_hex(8)` 生成的是 **16 个 hex 字符**，不是 8 个字符。

这不是功能 bug，但命名是错的。建议二选一：

```python
SNAPSHOT_ID_BYTES = 8
snapshot_id = secrets.token_hex(SNAPSHOT_ID_BYTES)
```

或者：

```python
SNAPSHOT_ID_LENGTH = 16
snapshot_id = secrets.token_hex(SNAPSHOT_ID_LENGTH // 2)
```

我推荐第一个，语义最诚实。

---

## 1.3 问题二：`mode` 有隐式 fallback

现在逻辑是：

```python
if mode == "focused":
    elements = focused_elements(elements, goal, limit)
```

如果 `mode="abc"`，它会被当成 full 处理。这个就是隐式 fallback。

虽然 schema 理论上会保证 mode 正确，但代码语义最好不要悄悄吞非法 mode。建议改成：

```python
if mode == "full":
    pass
elif mode == "focused":
    elements = focused_elements(elements, goal, limit)
else:
    raise ValueError(f"unsupported snapshot mode: {mode}")
```

这不是宽容校验，而是防止未来 schema 和实现漂移。

---

## 1.4 问题三：`limit` 对 full 没意义

现在：

```python
if limit is None:
    limit = SNAPSHOT_FOCUSED_DEFAULT_LIMIT if mode == "focused" else SNAPSHOT_MAX_LIMIT
```

但 `full` 分支根本不用 `limit`。这会让人误以为 full 最多 200 个元素。

建议只在 focused 下处理 limit：

```python
if mode == "focused":
    if limit is None:
        limit = SNAPSHOT_FOCUSED_DEFAULT_LIMIT
    else:
        limit = max(1, min(limit, SNAPSHOT_MAX_LIMIT))

    elements = focused_elements(elements, goal, limit)
```

`full` 就是 full，不要出现一个没用的 `SNAPSHOT_MAX_LIMIT` 参与感。

---

# 2. `snapshot_script.js` review

这才是主要问题。

## 2.1 最大问题：`isClickable()` 把“是否在视口内”混进了“是否可点击”

现在 `isClickable()` 里有：

```javascript
if (
  cx < 0 ||
  cy < 0 ||
  cx > window.innerWidth ||
  cy > window.innerHeight
) {
  return false;
}
```

这会导致：**视口外的按钮、链接不会被暴露成 ref。**

但你们已经决定不要 `viewport` 模式，`full snapshot` 应该表达页面可交互结构，而不是“当前屏幕能不能点到”。`click_ref` 本身会 `scroll_into_view_if_needed()`，所以 offscreen 元素也应该能暴露。

建议拆成两个概念：

```javascript
function isSemanticallyClickable(el) {
  // 判断它是不是按钮、链接、role=button、onclick、cursor:pointer 等
}

function getRectInfo(el) {
  // 单独返回 inViewport
}
```

`clickable` 不应该依赖 viewport。`inViewport` 可以作为 metadata 给 focused 排序用，但不能决定是否暴露。

---

## 2.2 第二大问题：大容器被当成 clickable

当前 `isClickable()` 用的是矩形中心点 + `elementFromPoint()`。如果一个大容器的中心点落在它的子元素上，这段逻辑会认为它可以点击：

```javascript
if (
  topEl &&
  topEl !== el &&
  !el.contains(topEl) &&
  !topEl.contains(el)
) {
  return false;
}
```

如果 `topEl` 是 `el` 的子元素，`el.contains(topEl)` 为 true，于是它被认为 clickable。

这就是为什么会出现类似：

```text
[e1] banner ...
```

这种大容器 ref。

**这对 agent 是强烈误导。**
banner、main、navigation、region、form 这些 landmark/container 不应该暴露成可操作 ref。

建议加一个排除表：

```javascript
const containerRoles = new Set([
  'banner',
  'navigation',
  'main',
  'contentinfo',
  'complementary',
  'region',
  'form',
  'group',
  'list',
  'listitem'
]);
```

然后：

```javascript
if (containerRoles.has(role)) return false;
```

除非它同时是 fillable，这类 landmark 不应该出现在 snapshot tree。

---

## 2.3 第三大问题：`shouldExpose()` 太宽

现在：

```javascript
function shouldExpose(el, role, visible, clickable, fillable) {
  if (!role || !visible) return false;

  if (role === 'iframe') return true;
  if (fillable) return true;
  if (clickable) return true;

  return false;
}
```

问题是 `clickable` 当前太宽，所以只要某个 role 容器被误判 clickable，就被暴露。

应该改成：

```javascript
function shouldExpose(el, role, visible, clickable, fillable, selectable, checkable) {
  if (!role || !visible) return false;

  if (role === 'iframe') return true;
  if (fillable) return true;
  if (selectable) return true;
  if (checkable) return true;
  if (clickable && isActionRole(role)) return true;

  return false;
}
```

其中：

```javascript
const actionRoles = new Set([
  'button',
  'link',
  'tab',
  'menuitem',
  'option',
  'checkbox',
  'radio',
  'combobox'
]);
```

不要让 `banner`、`main`、`form` 这种 role 因为“矩形可点”就暴露。

---

## 2.4 第四个问题：`role="textbox"` 没被识别为 fillable

JS 里的 `isFillable()` 只认：

```javascript
textarea
input
contenteditable
```

但没有认：

```javascript
[role="textbox"]
[role="searchbox"]
```

而你的 Python `FILLABLE_DESCENDANT_SELECTOR` 是认这些的。也就是说，snapshot 层和 action 层对 fillable 的定义不一致。

建议 JS 中补：

```javascript
const role = (el.getAttribute('role') || '').toLowerCase();

if (role === 'textbox' || role === 'searchbox') {
  return true;
}
```

否则 snapshot 可能不会标出一些自定义输入框 `[fillable]`。

---

## 2.5 第五个问题：label 规则对 link/button 太贪心

现在 `getLabel()` 会尝试：

```javascript
nearbyText
value
selfText
```

其中 `getNearbyText()` 会找：

```javascript
previousElementSibling
parent label
parent previous sibling
fieldContainer
```

这对 input 很有用，但对 link/button 很危险。比如一个 link 旁边或父容器里有一大坨营销文案，它就可能被标成：

```text
[e12] link 'Enter your email Sign up for GitHub'
```

然后 agent 误以为它是输入框。

建议分开：

```javascript
function getLabel(el, role, fillable) {
  if (fillable) {
    // aria / placeholder / label[for] / parent label / nearby text
  }

  if (role === 'button' || role === 'link') {
    // aria-label / title / own innerText
    // 不使用 fieldContainer nearbyText
  }

  // fallback
}
```

一句话：**nearbyText 只给表单控件用，不要给 link/button/container 用。**

---

## 2.6 第六个问题：`className: String(el.className || '')` 可能不稳

SVG 或某些元素的 `className` 不是普通字符串，可能是对象。你这里已经 `String()`，问题不大。只是既然 skip 了 SVG，大多数时候没事。

这个不是优先级问题。

---

# 3. 建议的修复优先级

## P0：禁止暴露 container/landmark ref

立刻改：

```javascript
banner
navigation
main
contentinfo
complementary
region
form
group
list
listitem
```

这些默认不暴露。

目标是避免再出现：

```text
[e1] banner ...
```

---

## P0：clickable 不要依赖 viewport

把当前 `isClickable()` 拆掉。
`clickable` 应该表示“语义上可点击”，不是“当前屏幕中心点能点到”。

当前 `full snapshot` 实际被 `viewport` 污染了，这和你们设计方向冲突。

---

## P0：link/button 的 label 不使用 nearbyText

否则 GitHub 首页这种“注册链接 + 输入提示”会继续误导模型。

---

## P1：`role=textbox/searchbox` 标成 fillable

保证 snapshot 和 fill_ref 对 fillable 的认知一致。

---

## P1：`snapshot.py` 修 mode 分支和 ID 命名

这两个不是导致 agent 乱填的根因，但属于工程质量问题，应该一起清掉。

---

# 4. 最小修改建议

## `snapshot.py`

改三点：

```python
SNAPSHOT_ID_BYTES = 8
```

```python
if mode == "full":
    pass
elif mode == "focused":
    if limit is None:
        limit = SNAPSHOT_FOCUSED_DEFAULT_LIMIT
    else:
        limit = max(1, min(limit, SNAPSHOT_MAX_LIMIT))
    elements = focused_elements(elements, goal, limit)
else:
    raise ValueError(f"unsupported snapshot mode: {mode}")
```

```python
snapshot_id = secrets.token_hex(SNAPSHOT_ID_BYTES)
```

---

## `snapshot_script.js`

核心方向：

```javascript
const containerRoles = new Set([
  'banner',
  'navigation',
  'main',
  'contentinfo',
  'complementary',
  'region',
  'form',
  'group',
  'list',
  'listitem'
]);

const actionRoles = new Set([
  'button',
  'link',
  'tab',
  'menuitem',
  'option',
  'checkbox',
  'radio',
  'combobox'
]);
```

`isFillable()` 补：

```javascript
const role = (el.getAttribute('role') || '').toLowerCase();

if (role === 'textbox' || role === 'searchbox') {
  return true;
}
```

`shouldExpose()` 改成：

```javascript
function shouldExpose(el, role, visible, clickable, fillable) {
  if (!role || !visible) return false;

  if (role === 'iframe') return true;
  if (fillable) return true;

  if (containerRoles.has(role)) return false;

  if (clickable && actionRoles.has(role)) return true;

  return false;
}
```

`isClickable()` 改成语义判断，不要再用 viewport center 决定：

```javascript
function isClickable(el, role) {
  if (isDisabled(el)) return false;

  const style = window.getComputedStyle(el);
  if (style.pointerEvents === 'none') return false;

  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();

  if (tag === 'a' && el.href) return true;
  if (tag === 'button') return true;

  if (tag === 'input') {
    return ['submit', 'button', 'reset', 'image'].includes(type);
  }

  if (['button', 'link', 'tab', 'menuitem', 'option'].includes(role)) {
    return true;
  }

  if (typeof el.onclick === 'function') return true;
  if (style.cursor === 'pointer') return true;

  return false;
}
```

然后调用处改成：

```javascript
const clickable = visible ? isClickable(el, role) : false;
```

---

# 5. 这轮 review 的结论

现在 snapshot 层的核心问题不是“抓不到元素”，而是 **抓得太宽、标签太暧昧、把容器暴露给了 agent**。

应该收紧为：

```text
只暴露真正可操作的元素：
  input / textarea / select / checkbox / radio / button / link / iframe

不要暴露布局容器：
  banner / main / nav / form / region / group / list

label 策略分角色：
  表单控件可以用 nearby label
  link/button 只用自身文本或 aria/title
```

修完这几个点，agent 再看到 GitHub 首页时，至少不会把 `banner` 和注册链接当成输入框。
