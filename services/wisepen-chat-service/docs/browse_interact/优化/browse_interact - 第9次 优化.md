明白，归档文档可以写成这种短版：

````md
# get_content 渲染内容提取调整记录

## 背景

原来的 `get_content` 逻辑是：

```python
html = await page.content()
cleaned = processor.process(html)
````

这会把浏览器当前页面当成静态 HTML 处理。但 `ContentProcessor` 主要服务静态抓取和文档解析，会做 readability 清洗、反爬关键词过滤、最小长度过滤等逻辑。这个逻辑适合 web fetch，不完全适合浏览器交互场景。

浏览器里的内容可能是登录后页面、JS 动态渲染内容、权限提示、验证码提示、短错误提示。这些内容即使很短，或者包含 `captcha / access denied / verify you are human`，也应该被返回给 agent，而不是被静态清洗器过滤掉。

## 调整内容

`get_content` 的主路径改为直接从浏览器渲染后的 DOM 中提取文本：

```text
page.evaluate(...)
-> 提取 main / article / [role=main] 等主要区域文本
-> 找不到主要区域时退回 document.body.innerText
-> 做轻量空白清洗
-> 返回 content
```

原来的静态处理路径保留为 fallback：

```text
如果浏览器内提取失败或返回空：
  page.content()
  -> ContentProcessor.process(html)
```

## 保持不变

本次不改变外部接口：

```json
{
  "action": {
    "type": "get_content"
  }
}
```

返回结构也保持不变：

```json
{
  "content": "...",
  "content_length": 1234
}
```

不新增依赖，不修改 `ContentProcessor`，不新增参数，不返回 HTML、diagnostics、source 等调试字段。

## 结果

调整后，`get_content` 更适合浏览器工具的定位：读取当前用户浏览器里实际渲染出来的可读内容。它可以更好处理登录态页面、动态页面、权限提示页、验证码提示页和短提示文本，同时保留静态 HTML 清洗作为兜底路径。

```
```
