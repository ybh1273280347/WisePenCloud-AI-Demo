有必要，而且这里应该作为 **`web_fetch` / OCR 下载 / 未来所有 URL 下载工具的统一安全入口** 来做，不要只补在 OCR 里。

现在这版：

```python
def is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
```

只能防明显非法 URL，**不能防 SSRF**。OWASP 对 SSRF 的核心建议就是：对服务端发起请求的用户 URL 做严格校验，限制协议、目标地址、重定向目标，并阻断内网/本地/云元数据地址等敏感目标。([cheatsheetseries.owasp.org][1])

---

# 建议目标

把 `utils/url.py` 升级成统一安全模块：

```text
web_fetch/utils/url.py
    DOCUMENT_EXTENSIONS
    is_valid_http_url
    is_document_url
    is_public_http_url
    validate_public_http_url
    resolve_public_host_ips
    is_private_or_reserved_ip
```

其中：

```text
is_valid_http_url:
    只做基础格式判断

is_public_http_url / validate_public_http_url:
    做 SSRF 安全校验

resolve_public_host_ips:
    解析 hostname，检查所有 IP 是否都是公网地址

is_private_or_reserved_ip:
    判断内网、本地、链路本地、保留地址、multicast 等
```

然后所有会发起外部请求的地方统一用：

```python
validate_public_http_url(url)
```

包括：

```text
WebFetchTool
StaticFetcher
LocalOcrProcessor._download_source
未来任何 URL 下载工具
```

---

# 要防什么

至少防这些：

```text
1. 非 http / https 协议
   file://
   ftp://
   gopher://
   dict://

2. localhost
   localhost
   localhost.
   *.localhost

3. 本地 / 内网 IP
   127.0.0.0/8
   10.0.0.0/8
   172.16.0.0/12
   192.168.0.0/16
   ::1
   fc00::/7
   fe80::/10

4. 云元数据地址
   169.254.169.254
   169.254.0.0/16

5. 保留 / 特殊地址
   0.0.0.0
   multicast
   unspecified
   reserved
   link-local

6. 用户名密码混淆
   http://127.0.0.1@evil.com
   http://evil.com@127.0.0.1

7. DNS 解析到内网地址
   attacker.com -> 127.0.0.1
   attacker.com -> 10.0.0.1

8. redirect 跳转到内网地址
```

其中 DNS rebinding 也要考虑：仅在请求前解析一次再让 HTTP client 自己重新解析，仍可能有 TOCTOU 风险。业界实践通常会要求解析并校验目标 IP、验证重定向目标，并避免盲目跟随 redirect。近期 Pydantic AI 的 SSRF 修复说明里也明确提到：默认阻断 private/internal IP、阻断云元数据端点、只允许 http/https、请求前解析 hostname 防 DNS rebinding、并验证每个 redirect target。([GitHub][2])

---

# 推荐实现

## 1. 新增安全错误类型

```python
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
import socket
from urllib.parse import urlparse

__all__ = [
    "DOCUMENT_EXTENSIONS",
    "UrlSecurityError",
    "is_valid_http_url",
    "is_document_url",
    "is_public_http_url",
    "validate_public_http_url",
    "resolve_public_host_ips",
    "is_private_or_reserved_ip",
]
```

```python
class UrlSecurityError(ValueError):
    pass
```

这里用异常比 bool 更好，因为调用方可以拿到具体原因。但保留 `is_public_http_url()` 给简单判断。

---

## 2. IP 判断

```python
BLOCKED_IP_NETWORKS = tuple(
    ip_network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "100::/64",
        "2001::/23",
        "2001:db8::/32",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)
```

```python
def is_private_or_reserved_ip(value: str) -> bool:
    try:
        ip = ip_address(value)
    except ValueError:
        return False

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True

    return any(ip in network for network in BLOCKED_IP_NETWORKS)
```

注意：`ipaddress` 对很多特殊范围已经覆盖，但显式列出 cloud metadata / benchmark / documentation / NAT shared 等范围更清楚。安全检查清单里也通常会明确阻断 internal IP、cloud metadata、IPv6 internal addresses。([GitHub][3])

---

## 3. hostname 基础阻断

```python
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}

BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
)
```

```python
def normalize_hostname(hostname: str) -> str:
    return hostname.strip().strip(".").lower()
```

```python
def is_blocked_hostname(hostname: str) -> bool:
    normalized = normalize_hostname(hostname)

    if normalized in BLOCKED_HOSTNAMES:
        return True

    return normalized.endswith(BLOCKED_HOST_SUFFIXES)
```

`.local` 是否要阻断看你环境。如果有 mDNS / 内网服务，建议阻断。

---

## 4. DNS 解析并检查所有结果

```python
def resolve_public_host_ips(hostname: str) -> list[str]:
    normalized = normalize_hostname(hostname)

    if is_blocked_hostname(normalized):
        raise UrlSecurityError("blocked hostname")

    try:
        ip = ip_address(normalized)
    except ValueError:
        ip = None

    if ip is not None:
        if is_private_or_reserved_ip(str(ip)):
            raise UrlSecurityError("blocked IP address")
        return [str(ip)]

    try:
        infos = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UrlSecurityError(f"hostname could not be resolved: {normalized}") from e

    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise UrlSecurityError(f"hostname resolved to no addresses: {normalized}")

    blocked_ips = [ip for ip in ips if is_private_or_reserved_ip(ip)]
    if blocked_ips:
        raise UrlSecurityError(f"hostname resolves to blocked IP address: {blocked_ips[0]}")

    return ips
```

这里策略是：**只要任意解析结果是 blocked，就拒绝整个 hostname**。这比“挑一个公网 IP 用”更稳。

---

## 5. URL 校验函数

```python
def validate_public_http_url(url: str) -> str:
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise UrlSecurityError("URL scheme must be http or https")

    if not parsed.netloc or not parsed.hostname:
        raise UrlSecurityError("URL host is missing")

    if parsed.username or parsed.password:
        raise UrlSecurityError("URL userinfo is not allowed")

    if parsed.port is not None:
        if parsed.port <= 0 or parsed.port > 65535:
            raise UrlSecurityError("URL port is invalid")

        allowed_ports = {80, 443}
        if parsed.port not in allowed_ports:
            raise UrlSecurityError("URL port is not allowed")

    resolve_public_host_ips(parsed.hostname)

    return url
```

端口策略这里有两种：

```text
严格：
    只允许 80 / 443

宽松：
    允许 http/https 任意端口，但仍阻断内网 IP
```

我建议第一版严格一点，只允许 80/443。
如果你需要抓 `:8080` 的公开网页，可以加 settings：

```python
WEB_FETCH_ALLOWED_PORTS = (80, 443)
```

但别一开始开太宽。

---

## 6. bool 包装函数

```python
def is_public_http_url(url: str) -> bool:
    try:
        validate_public_http_url(url)
        return True
    except UrlSecurityError:
        return False
```

`is_valid_http_url()` 保持旧语义：

```python
def is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
```

但真正用于下载前校验的是：

```python
validate_public_http_url(url)
```

---

# redirect 怎么处理

这是重点。现在 `LocalOcrProcessor._download_source()` 里：

```python
httpx.AsyncClient(..., follow_redirects=True)
```

这会让 HTTP client 自动跟随 redirect。这样你只能校验初始 URL，**不能校验每一次 redirect 后的新 URL**。

建议改成：

```python
follow_redirects=False
```

手动处理 3xx：

```python
MAX_REDIRECTS = 5
```

每次拿到 `Location`：

```python
from urllib.parse import urljoin

redirect_url = urljoin(str(response.url), location)
validate_public_http_url(redirect_url)
```

然后继续请求。

也就是说，**每个 redirect target 都必须重新安全校验**。这是 SSRF 防护里非常关键的一步，很多 SSRF 绕过就是靠 redirect 到内网地址。相关安全修复也会强调验证 redirect target。([GitHub][2])

---

# DNS rebinding 怎么处理

完整防 DNS rebinding 最严谨的方案是：

```text
1. 请求前 resolve hostname
2. 校验解析 IP
3. 连接时固定到已校验 IP
4. Host header / SNI 保持原 hostname
5. redirect 每次重复上述流程
```

这个实现复杂度较高，尤其是 httpx 自定义 transport / DNS resolver。

我建议分阶段：

```text
当前第一步：
    validate_public_http_url 做 DNS 解析校验
    redirect 手动校验
    阻断明显内网 / metadata / localhost / 私有地址

后续加强：
    实现 resolved-IP transport，或在网络层 egress policy 阻断内网
```

如果项目运行在容器环境，**网络层 egress 防护**非常重要：代码层校验可能被 DNS rebinding 绕过，网络层阻断访问内网 / metadata endpoint 是第二道防线。

---

# 对现有 OCR 的具体修改

`LocalOcrProcessor._extract_from_url()` 当前有：

```python
parsed = urlparse(url)
if parsed.scheme not in {"http", "https"} or not parsed.netloc:
    raise OcrProcessingError("OCR only supports http and https URLs.")
```

建议改成：

```python
try:
    validate_public_http_url(url)
except UrlSecurityError as e:
    raise OcrProcessingError(f"OCR URL rejected: {e}") from e
```

`_download_source()` 里 `follow_redirects=True` 改成手动 redirect。

---

# 对 WebFetchTool 的修改

`WebFetchTool` 当前用：

```python
if not is_valid_http_url(url):
    return "[Tool Error] Invalid url parameter. URL must start with http:// or https://."
```

建议改成：

```python
try:
    validate_public_http_url(url)
except UrlSecurityError as e:
    return f"[Tool Error] URL rejected by security policy: {e}"
```

然后后续 `StaticFetcher` / `SteelFetcher` / `LocalScriptFetcher` 最好也用同一策略，避免绕过。

---

# 给 Codex 的提示词

```text
请加强 web_fetch/utils/url.py 的 URL 安全策略，用于统一防 SSRF。不要只校验 scheme/netloc。

背景：
当前 is_valid_http_url 只判断 http/https 和 netloc，无法防 SSRF。
web_fetch、StaticFetcher、OCR 下载都会根据用户 URL 由服务端发起请求，因此必须统一 URL 安全策略。
目标是阻断 localhost、内网 IP、link-local、cloud metadata、reserved/multicast 地址、带 userinfo 的 URL，以及 redirect 到不安全目标。

一、保留旧函数

保留：
- DOCUMENT_EXTENSIONS
- is_valid_http_url
- is_document_url

is_valid_http_url 继续只做基础 http/https + netloc 判断，避免大面积破坏现有调用。

二、新增安全类型和函数

在 web_fetch/utils/url.py 新增：

class UrlSecurityError(ValueError):
    pass

新增：
- is_private_or_reserved_ip(value: str) -> bool
- normalize_hostname(hostname: str) -> str
- is_blocked_hostname(hostname: str) -> bool
- resolve_public_host_ips(hostname: str) -> List[str]
- validate_public_http_url(url: str) -> str
- is_public_http_url(url: str) -> bool

三、IP 阻断策略

使用 ipaddress 模块。

阻断：
- private
- loopback
- link-local
- multicast
- reserved
- unspecified
- cloud metadata / link-local 169.254.0.0/16
- IPv6 loopback / unique local / link-local / multicast
- 0.0.0.0/8
- 100.64.0.0/10
- 198.18.0.0/15
- documentation / benchmark / reserved ranges

建议定义 BLOCKED_IP_NETWORKS。

四、Hostname 阻断

阻断：
- localhost
- localhost.
- *.localhost
- *.local

normalize hostname 时：
- strip
- strip trailing dot
- lower

五、DNS 解析

resolve_public_host_ips(hostname):
- 如果 hostname 本身是 IP，直接检查 IP。
- 如果是域名，使用 socket.getaddrinfo 解析。
- 解析结果为空则拒绝。
- 只要任意解析 IP 属于 blocked IP，就拒绝整个 hostname。
- 返回解析到的 IP 字符串列表。

六、validate_public_http_url

要求：
- scheme 必须是 http 或 https
- 必须有 hostname
- 不允许 username/password userinfo
- port 如存在，必须合法
- 第一版建议只允许 80/443 端口
- 调用 resolve_public_host_ips(parsed.hostname)
- 成功返回原 url

七、WebFetchTool 修改

将 WebFetchTool 中：
is_valid_http_url(url)

替换为：
try:
    validate_public_http_url(url)
except UrlSecurityError as e:
    return f"[Tool Error] URL rejected by security policy: {e}"

八、LocalOcrProcessor 修改

_extract_from_url 中不再手写 scheme/netloc 校验。
改为：
try:
    validate_public_http_url(url)
except UrlSecurityError as e:
    raise OcrProcessingError(f"OCR URL rejected: {e}") from e

_download_source 中不要使用 follow_redirects=True。
改为 follow_redirects=False，并手动处理最多 5 次 redirect。

每次遇到 3xx：
- 读取 Location
- 使用 urllib.parse.urljoin(response.url, location) 生成 redirect_url
- 对 redirect_url 调用 validate_public_http_url
- 校验通过后继续请求
- 超过 5 次返回 OcrProcessingError("too many redirects")

九、StaticFetcher / 其他 Fetcher

检查 StaticFetcher 是否也直接请求用户 URL。
如果是，也应在请求前调用 validate_public_http_url。
SteelFetcher / LocalScriptFetcher 如会访问用户 URL，也应在入口处至少复用同一校验策略，避免绕过。

十、不要做

1. 不要删除 is_valid_http_url。
2. 不要让 is_valid_http_url 变成强安全校验，避免旧调用语义突变。
3. 不要只靠正则判断 URL。
4. 不要允许 userinfo。
5. 不要盲目 follow_redirects=True。
6. 不要只检查初始 URL，不检查 redirect target。
7. 不要在 OCR 里单独实现一套 URL 安全逻辑。
8. 不要使用 getattr(settings, "...", default)。

十一、测试建议

补充 URL 安全单测：
- http://example.com 通过
- https://example.com/path 通过
- file:///etc/passwd 拒绝
- http://localhost 拒绝
- http://localhost. 拒绝
- http://127.0.0.1 拒绝
- http://[::1] 拒绝
- http://10.0.0.1 拒绝
- http://192.168.1.1 拒绝
- http://169.254.169.254 拒绝
- http://0.0.0.0 拒绝
- http://example.com:22 拒绝
- http://user:pass@example.com 拒绝
- 域名解析到内网 IP 时拒绝
- redirect 到 127.0.0.1 时拒绝
```

---

# 最终建议

现在应该做的是：

```text
1. utils/url.py 保留旧基础校验
2. 新增 validate_public_http_url 作为安全校验
3. web_fetch / OCR / static fetcher 全部使用安全校验
4. OCR 下载禁止自动 follow_redirects
5. redirect target 每跳重新校验
```

这不是过度防御。因为 `web_fetch` 和 OCR 都是**服务端根据用户输入主动访问 URL**，这是 SSRF 的典型入口。

[1]: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html?utm_source=chatgpt.com "Server-Side Request Forgery Prevention Cheat Sheet"
[2]: https://github.com/pydantic/pydantic-ai/security/advisories/GHSA-2jrp-274c-jhv3?utm_source=chatgpt.com "Server-Side Request Forgery (SSRF) in URL Download ..."
[3]: https://github.com/getsentry/skills/blob/main/skills/security-review/references/ssrf.md?utm_source=chatgpt.com "skills/skills/security-review/references/ssrf.md at main"
