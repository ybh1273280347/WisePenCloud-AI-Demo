# WebFetch CLI - 本地独立的网页抓取工具

三级降级策略的网页抓取工具，从原项目提取出来的独立版本，方便在本地私用！

## 特性

- ✅ **三级降级抓取策略**：
  1. 静态 HTTP 抓取（快速）
  2. Steel API 浏览器抓取（强大）
  3. 本地 Playwright 浏览器抓取（兜底）
- ✅ **内容清洗与 Markdown 转换**
- ✅ **单 URL 和批量模式**
- ✅ **保存到文件**
- ✅ **精美的 CLI 界面（基于 typer + rich）**
- ✅ **完全独立，不耦合原项目**
- ✅ **Steel 默认用本地 3000 端口**

## 安装步骤

### 1. 安装 Node.js 依赖

```bash
# 在 webfetch-cli 目录下
cd webfetch-cli
npm install
```

这会安装：
- `rebrowser-playwright`: 强大的浏览器自动化库
- `turndown`: HTML 转 Markdown

### 2. 安装 Python 依赖

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -e .
```

这会安装：
- `httpx`: 异步 HTTP 客户端
- `steel-sdk`: Steel API 客户端
- `readability-lxml`: 内容提取
- `markdownify`: HTML 转 Markdown
- `loguru`: 日志
- `typer[all]`: 精美的 CLI 界面

### 3. 准备 Steel API（可选）

默认已配置为 `http://localhost:3000`，如有需要可在使用时通过 `-s` 修改。
如果没有 Steel API，工具仍然可以运行，会自动降级到本地 Playwright 抓取。

## 使用方法

### 基础用法 - 单 URL 抓取（最简单！）

```bash
# 抓取并输出到终端
webfetch https://example.com

# 保存到文件
webfetch https://example.com -o output.md
```

### 强制使用浏览器模式

```bash
# 跳过静态抓取，直接用浏览器
webfetch https://example.com -f
```

### 用 single 子命令

```bash
webfetch single https://example.com
webfetch single https://example.com -o result.md
```

### 批量抓取

```bash
# 从文件读取 URLs（每行一个）
webfetch batch -i urls.txt -d output/

# 直接传多个 URL
webfetch batch https://a.com https://b.com https://c.com -d output/
```

### 调整超时和配置

```bash
# 自定义配置（所有参数都有合理默认值）
webfetch \
  -m 200 \
  --static-timeout 10 \
  --browser-timeout 120 \
  https://example.com
```

### 查看帮助

```bash
webfetch --help
webfetch single --help
webfetch batch --help
```

## 项目结构

```
webfetch-cli/
├── pyproject.toml       # Python 项目配置
├── package.json         # Node.js 依赖
├── local_web_fetcher.js # 本地浏览器抓取脚本
└── src/
    └── webfetch/
        ├── __init__.py
        ├── cli.py       # CLI 入口
        ├── logger.py    # 日志工具
        ├── content_cleaner.py # 内容清洗
        ├── fetch_coordinator.py  # 核心调度器
        └── fetcher/
            ├── __init__.py
            ├── static_fetcher.py   # 静态抓取
            ├── steel_fetcher.py    # Steel API 抓取
            └── local_fetcher.py    # 本地浏览器抓取
```

## 作为库使用

除了 CLI，你也可以直接在代码中使用：

```python
import asyncio
from webfetch import FetchCoordinator

async def main():
    coordinator = FetchCoordinator(
        steel_base_url="http://localhost:3000",
        min_content_length=400,
    )
    
    content = await coordinator.fetch("https://example.com")
    if content:
        print(content)

asyncio.run(main())
```

## 常见问题

### Q: Steel API 无法连接怎么办？

没关系，工具会自动降级到本地 Playwright 抓取，一样能用。

### Q: 本地 Playwright 报错找不到浏览器？

首次运行前需要安装浏览器：
```bash
npx playwright install chromium
```

### Q: 可以完全不依赖 Steel API 吗？

可以！直接用本地浏览器模式即可，每次加上 `--force-browser` 或在代码中设置。

### Q: 如何禁用日志？

可以在代码中修改 logger 配置，或重定向输出到 `/dev/null`。

## 许可证

保持与原项目一致。
