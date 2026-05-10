# web_fetch 第 1 次修复：本地浏览器代理继承

## 背景

在本地测试中出现过：

```text
用户自己的浏览器能打开 GitHub，但工具测试打不开 GitHub。
```

这类问题通常不是目标网站不可访问，而是工具启动的浏览器网络环境与用户电脑浏览器不一致。

## 问题

`local_web_fetcher.js` 的浏览器启动参数曾包含：

```text
--proxy-server=direct://
```

该参数会强制 Chromium 直连，绕过系统代理或用户常用代理配置。

结果是：

```text
用户浏览器走代理，可以访问。
本地 fetcher 强制 direct，不走代理，访问失败。
```

## 修复

删除强制直连参数。

新增环境代理读取：

```text
HTTPS_PROXY / https_proxy
HTTP_PROXY / http_proxy
ALL_PROXY / all_proxy
NO_PROXY / no_proxy
```

如果存在代理环境变量，则传给 Playwright：

```javascript
launchOptions.proxy = {
  server,
  username,
  password,
  bypass
}
```

如果不存在代理环境变量，则不显式设置 proxy，保留 Chromium 默认系统代理行为。

## 修复原则

```text
工具浏览器应尽量继承用户电脑网络环境。
不要默认强制 direct。
不要在 tool output 暴露代理配置。
```

## 影响范围

影响：

- `web_fetch` 本地 JS fetcher。
- 依赖本地 Chromium 抓取的兜底链路。

不影响：

- StaticFetcher。
- SteelFetcher。
- WebFetchTool 对外协议。

## 验证

已执行：

```bash
node --check services/wisepen-chat-service/src/chat/application/web_fetch/local_web_fetcher.js
```

真实网络访问结果仍取决于用户机器代理、DNS、系统证书和目标网站策略。

