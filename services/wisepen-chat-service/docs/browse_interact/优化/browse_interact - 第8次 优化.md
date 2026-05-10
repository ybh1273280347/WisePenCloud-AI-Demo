# browse_interact 第 8 次优化：任务完成率增强

## 优化原则

本轮优化遵循：

```text
先增强任务完成率
再增强可维护性
最后再增强能力边界
```

因此没有继续做大重构，而是在当前 `snapshot/ref` 架构上补足能直接提升网页任务完成率的能力。

## 1. snapshot label 提取增强

### 问题

很多网页的可交互元素没有直接文本：

```html
<label>Email</label>
<input />
```

如果 snapshot 只看元素自身，agent 只能看到：

```text
[e3] textbox [fillable]
```

这会降低 ref 选择准确率。

### 优化

`snapshot_script.js` 的 label 提取顺序增强为：

```text
aria-label
placeholder
title
alt
name
aria-labelledby
aria-describedby
label[for=id]
父级 label
附近文本
input value
自身文本
```

附近文本包括：

- 前一个 sibling。
- 父容器里的 label。
- 父容器前一个 sibling。
- 常见 form/field 容器文本。

目标输出：

```text
[e3] textbox "Email" [fillable]
```

## 2. focused snapshot 规则增强

### 当前策略

`focused` 仍然是确定性规则，不引入 embedding 或 LLM rerank。

评分输入字段包括：

```text
label
placeholder
ariaLabel
name
id
title
role
type
ancestorText
tag
className
```

字段权重优先级：

```text
label / placeholder / ariaLabel 最高
name / id 次之
className 最低
```

### 语义规则

增加中英文意图归一：

```text
search / find / query / 搜索 / 查询
login / sign in / 登录 / 登陆
password / 密码
submit / confirm / continue / 提交 / 确认 / 继续
email / 邮箱 / 邮件
username / 用户名 / 账号
```

真实 `fillable` 元素优先于有 `has-fillable-descendant` 的容器。这样搜索框场景中，`input[type=search]` 会排在 `role=search` 容器前。

## 3. snapshot tree 格式稳定

tree 输出收敛为短格式：

```text
[e1] textbox "Search" [fillable]
[e2] button "Search"
[e3] link "Sign in"
```

不向 agent 输出：

```text
x/y/width/height
DOM path
CSS selector
internal score
diagnostics
count
```

这保持了 agent-facing 协议干净，也降低模型误用内部字段的概率。

## 4. select_ref

新增：

```json
{
  "action": {
    "type": "select_ref",
    "snapshot_id": "...",
    "ref": "e4",
    "value": "cn"
  }
}
```

也支持按可见 label：

```json
{
  "action": {
    "type": "select_ref",
    "snapshot_id": "...",
    "ref": "e4",
    "label": "China"
  }
}
```

行为合同：

- 需要已有 session。
- 需要当前 snapshot_id。
- 成功后 invalidate snapshot。
- 如果 ref 指向 select 容器，会向下寻找内部 select。
- 成功响应不返回 option 文本，只返回 `selected_count`。

## 5. check_ref

新增：

```json
{
  "action": {
    "type": "check_ref",
    "snapshot_id": "...",
    "ref": "e5",
    "checked": true
  }
}
```

行为合同：

- 需要已有 session。
- 需要当前 snapshot_id。
- 支持 checkbox / radio。
- 成功后 invalidate snapshot。
- 如果 ref 指向容器，会向下寻找 checkbox/radio。

这比让 agent 用 `click_ref` 猜状态更稳定。

## 6. 保持克制

本轮没有加入：

```text
drag
hover
double_click
right_click
mouse_move
pixel_click
多 snapshot mode
复杂 DOM path
LLM rerank
```

这些能力会把工具拉回低层 UI 自动化，暂时不符合任务完成率优先的路线。

