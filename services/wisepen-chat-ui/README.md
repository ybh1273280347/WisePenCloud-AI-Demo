# WisePen Chat UI

普通 Chat 前端，使用真实 `/chat/completions` SSE。工具调用事件会以内嵌卡片出现在 assistant 消息流里。

## 启动

```powershell
cd services/wisepen-chat-ui
copy .env.example .env
npm install
npm run dev
```

默认开发模式走 Vite 代理：

```env
VITE_CHAT_API_BASE_URL=
VITE_CHAT_PROXY_TARGET=http://localhost:9200
VITE_FROM_SOURCE_SECRET=APISIX-wX0iR6tY
VITE_DEV_SERVER_HOST=0.0.0.0
VITE_DEV_SERVER_PORT=5173
```

`VITE_CHAT_API_BASE_URL` 留空时，浏览器请求同源 `/chat/...`，再由 Vite 代理转发到 `VITE_CHAT_PROXY_TARGET`。这样可以避免浏览器跨域预检被后端 `X-From-Source` 安全中间件拦截。

如果你确认后端 CORS 和安全头允许浏览器直连，也可以把 `VITE_CHAT_API_BASE_URL` 改为后端地址。

Docker Compose 会把 `chat-service` 的容器端口 `9200` 发布到宿主机 `${CHAT_SERVICE_PORT:-9200}`，所以本机启动的前端默认可以代理到 `http://localhost:9200`。

## SSE 映射

- `text-delta` 追加 assistant 文本。
- `tool-input-start` 创建 running 工具卡片。
- `tool-input-available` 按 `toolCallId` 更新 input。
- `tool-output-available` 按 `toolCallId` 更新 output。
- output 包含 `[Tool Error]` 时，工具卡片显示 error。

没有 mock SSE，没有内置 prompt，没有调试工作台。

## 已接入后端功能

- `/chat/model/listModels`：模型列表与模型选择，发送消息时写入真实 `model` 字段。
- `/chat/session/listSessions`：会话列表。
- `/chat/session/createSession`：新建会话。
- `/chat/session/listHistoryMessages`：切换会话时加载历史消息，并恢复文本与工具卡片。
- `/chat/session/renameSession`：重命名会话。
- `/chat/session/pinSession`：置顶/取消置顶会话。
- `/chat/session/deleteSession`：删除当前会话。
- `/chat/memory/listMemories`：长期记忆列表。
- `/chat/memory/deleteMemory`：删除单条记忆。
- `/chat/memory/deleteAllMemories`：清空长期记忆。
