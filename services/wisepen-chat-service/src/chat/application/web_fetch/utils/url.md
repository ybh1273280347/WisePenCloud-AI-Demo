## 结论

`web_fetch/utils/url.py` 的安全策略整体很强：它限制协议为 `http/https`，拒绝用户信息、非法端口、反斜杠、控制字符，解析域名后阻断私有 / 保留 / 回环 / 链路本地地址，并且对 fake-ip 场景做 DoH 二次解析。`DOCUMENT_EXTENSIONS` 也已经和 `document_parse` 当前支持范围对齐：`.pdf/.docx/.pptx/.xls/.xlsx/.ods/.epub`，没有 `.doc/.ppt`。

当前需要处理的是 **3 个确定点**：

```text
1. 模型可见错误信息现在是中文，需要回滚为英文。
2. validate_public_http_url() 里 url.strip() 是否保留：这里建议保留。
3. 删除未使用的 _is_valid_http_url。
```

---

## 第三方库 API 确认

这个文件使用了 `dnspython` 的 `dns.message.make_query()` 和 `dns.query.https()` 做 DoH 查询。dnspython 官方文档说明，`dns.message.make_query(qname, rdtype)` 会创建 DNS query message，`dns.query` 模块负责发送 DNS query 并处理响应，当前 DoH 查询方式是合理的。([dnspython][1])

`urllib.parse.urlparse()` 只负责把 URL 拆成组件，不是完整安全校验器，所以当前代码在 `urlparse()` 后继续检查 scheme、netloc、hostname、username/password、port、hostname 字符和解析 IP，这个方向是对的。Python 官方文档也说明 `urllib.parse` 是 URL 组件解析接口，不应把它等同于安全校验。([Python documentation][2])

`ipaddress.ip_address()` / `ip_network()` 用来识别 IP 字面量、私有网段、保留网段等也合适；Python 官方文档明确 `ipaddress` 提供 IPv4/IPv6 地址与网络处理能力。([Python documentation][3])

---

## 我确定要改的点

### 1. 错误信息回滚为英文

`UrlSecurityError` 会被 `WebFetchTool` 捕获并返回给模型：

```text
[Tool Error] URL rejected by security policy: ...
```

所以 `UrlSecurityError` 的 message 属于模型可见上下文，应该保持英文。

当前示例：

```python
raise UrlSecurityError("URL 为空")
raise UrlSecurityError("URL 端口不被允许")
raise UrlSecurityError(f"域名解析为受阻止的 IP 地址: {blocked_ip}")
```

应改为英文：

```python
raise UrlSecurityError("URL is empty")
raise UrlSecurityError("URL port is not allowed")
raise UrlSecurityError(f"Hostname resolves to a blocked IP address: {blocked_ip}")
```

---

### 2. `validate_public_http_url()` 里的 `url.strip()` 建议保留

虽然我们通常不做宽容输入，但这里是安全边界函数，不是普通业务函数。保留：

```python
url = url.strip()
```

是可以接受的。

原因是它会先规范化边缘空白，再继续检查：

```python
if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
    ...
```

这样：

```text
" https://example.com "
```

会按安全 URL 处理；而：

```text
"https://exa mple.com"
```

仍会因为内部空白被拒绝。

这个函数是 URL 安全入口，保留 `strip()` 不会削弱安全策略。

---

### 3. 删除未使用的 `_is_valid_http_url()`

当前：

```python
def _is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
```

这个函数没有被当前文件后续逻辑使用，而且它的校验明显弱于 `validate_public_http_url()`。建议删除，避免误导后续调用方使用弱校验。

---

## 建议修改后的关键代码

只列需要改的部分。

```python
class UrlSecurityError(ValueError):
    pass
```

```python
def _resolve_with_system_dns(hostname: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UrlSecurityError(f"Hostname cannot be resolved: {hostname}") from e

    return sorted({info[4][0] for info in infos})
```

```python
def _resolve_fake_ip_hostname(hostname: str) -> List[str]:
    trusted_ips = _resolve_with_doh(hostname)
    if not trusted_ips:
        raise UrlSecurityError(
            f"Hostname resolved to fake-ip and DoH could not resolve a real address: {hostname}"
        )

    blocked_ip = _find_blocked_ip(trusted_ips)
    if blocked_ip:
        raise UrlSecurityError(
            f"Hostname resolves to a blocked IP address through DoH: {blocked_ip}"
        )

    return trusted_ips
```

```python
def _validate_hostname(hostname: str) -> str:
    normalized = _normalize_hostname(hostname)

    if not normalized:
        raise UrlSecurityError("URL is missing a hostname")

    if _has_invalid_hostname_chars(normalized):
        raise UrlSecurityError("URL hostname contains invalid characters")

    if _is_blocked_hostname(normalized):
        raise UrlSecurityError("Hostname is blocked")

    return normalized
```

```python
def _resolve_public_host_ips(hostname: str) -> List[str]:
    normalized = _validate_hostname(hostname)

    try:
        ip = ip_address(normalized)
    except ValueError:
        ip = None

    if ip is not None:
        if _is_private_or_reserved_ip(str(ip)):
            raise UrlSecurityError("IP address is blocked")

        return [str(ip)]

    system_ips = _resolve_with_system_dns(normalized)
    if not system_ips:
        raise UrlSecurityError(f"Hostname did not resolve to any address: {normalized}")

    if _all_fake_ips(system_ips):
        return _resolve_fake_ip_hostname(normalized)

    blocked_ip = _find_blocked_ip(system_ips)
    if blocked_ip:
        raise UrlSecurityError(f"Hostname resolves to a blocked IP address: {blocked_ip}")

    return system_ips
```

```python
def validate_public_http_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise UrlSecurityError("URL is empty")

    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise UrlSecurityError("URL contains invalid characters")

    if "\\" in url:
        raise UrlSecurityError("URL must not contain backslashes")

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise UrlSecurityError("URL scheme must be http or https")

    if not parsed.netloc or not parsed.hostname:
        raise UrlSecurityError("URL is missing a hostname")

    if parsed.username or parsed.password:
        raise UrlSecurityError("URL must not contain userinfo")

    try:
        port = parsed.port
    except ValueError as e:
        raise UrlSecurityError("URL port is invalid") from e

    if port is not None:
        if port <= 0 or port > 65535:
            raise UrlSecurityError("URL port is invalid")

        if port not in {80, 443}:
            raise UrlSecurityError("URL port is not allowed")

    _resolve_public_host_ips(parsed.hostname)

    return url
```

---

## 需要人工确认后再小修的点

### 1. 是否允许端口 `8080 / 8443`

当前只允许：

```python
{80, 443}
```

这是非常严格但安全的策略。对公开网页抓取来说可能会拒绝一些合法公开服务，比如 `https://example.com:8443`。但这是 URL 安全策略，不应随手放宽。

当前建议保持 `{80, 443}`。

---

### 2. DoH 失败时是否应 fail closed

当前 fake-ip 场景下，如果系统 DNS 只解析出 fake IP，DoH 获取不到真实地址，会抛 `UrlSecurityError`。这是 fail closed，安全上正确。

不要改成“DoH 失败就放行”。

---

### 3. 是否 blocking DNS 需要线程化

`validate_public_http_url()` 内部有 `socket.getaddrinfo()` 和 DoH 请求，都是阻塞调用。现在 `FetchCoordinator` 是 async 函数，但你之前在 tool 层删掉了 `asyncio.to_thread(validate_public_http_url, url)`，如果 coordinator 里直接同步调用，会阻塞事件循环。

所以需要确认 `fetch_coordinator.py` 当前是：

```python
validate_public_http_url(url)
```

还是：

```python
await asyncio.to_thread(validate_public_http_url, url)
```

如果是同步调用，建议改成 `await asyncio.to_thread(...)`。这不改变安全策略，只是避免阻塞 event loop。

这是直接关联文件，需要回到 `fetch_coordinator.py` 小修。

---

## 暂不建议改的点

### 1. 不建议删除 fake-ip + DoH 逻辑

这个逻辑是为了解决本地 DNS 返回 fake IP 的场景，属于安全边界的一部分。保留。

### 2. 不建议简化私网判断

当前既用了 `ip.is_private / is_loopback / is_link_local / is_multicast / is_reserved / is_unspecified`，又用了显式 blocked networks，属于安全冗余。可以保留。

### 3. 不建议继续导出更多 helper

当前只有 `DOCUMENT_EXTENSIONS`、`UrlSecurityError`、`validate_public_http_url`、`is_public_http_url` 是外部合理使用点。其他保持内部函数即可。

---

## 本文件验收标准

```bash
rg "_is_valid_http_url" src/chat/application/web_fetch/utils/url.py
```

应无结果。

```bash
rg "URL 为空|缺少主机名|受阻止|端口不被允许|域名|无法解析|无效字符" src/chat/application/web_fetch/utils/url.py
```

应无结果，`UrlSecurityError` 文本应为英文。

```bash
rg "\"\\.doc\"|\"\\.ppt\"" src/chat/application/web_fetch/utils/url.py
```

不应命中。

```bash
rg "\"\\.epub\"|\"\\.ods\"" src/chat/application/web_fetch/utils/url.py
```

应命中。

人工确认：

```text
validate_public_http_url 返回 strip 后的 URL。
validate_public_http_url 只允许 http/https。
validate_public_http_url 禁止 userinfo。
validate_public_http_url 只允许 80/443。
DNS 解析到私网 / 保留 / 回环地址会拒绝。
fake-ip 场景下 DoH 失败会拒绝。
```

---

## 待办更新

```text
1. 模型可见中文错误文本需要回滚为英文。
2. PaddleOCR worker 同时兼容 v3 和 legacy API，需要确认依赖版本后处理。
3. FetchedDocument 应考虑后续移到 web_fetch/models.py，但当前不处理。
4. StaticFetcher 是否支持 Content-Disposition 文件名，等实际需求再处理。
5. StaticFetcher 是否需要 charset-aware decode，等乱码样本再处理。
6. FetchCoordinator 中 URL 安全校验如果是同步调用，需要改为 asyncio.to_thread。
```

## 下一个建议 review 文件

```text
chat/application/web_fetch/fetch_coordinator.py
```

原因：`url.py` 本身安全策略基本成立，但 `validate_public_http_url()` 是阻塞函数。需要回到 coordinator 确认它是否用 `asyncio.to_thread()` 调用安全校验，否则 async fetch 入口会阻塞事件循环。

[1]: https://dnspython.readthedocs.io/en/latest/message-make.html?utm_source=chatgpt.com "Making DNS Messages — dnspython 2.9.0 documentation"
[2]: https://docs.python.org/3/library/urllib.parse.html?utm_source=chatgpt.com "urllib.parse — Parse URLs into components — Python 3.14 ..."
[3]: https://docs.python.org/3/library/ipaddress.html?utm_source=chatgpt.com "ipaddress — IPv4/IPv6 manipulation library"
