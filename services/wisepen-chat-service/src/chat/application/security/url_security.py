import socket
from ipaddress import ip_address, ip_network
from typing import List
from urllib.parse import urlparse

import dns.message
import dns.query
import dns.rdatatype

from chat.core.config.tool_settings import tool_settings
from common.logger import log_fail


class UrlSecurityError(Exception):
    pass


# SSRF 防护黑名单：
# - RFC1918 私有网段：10/8, 172.16/12, 192.168/16
# - loopback / link-local / multicast / reserved / documentation ranges
# - IPv6 loopback / unique-local / link-local / multicast / documentation ranges
# - 198.18.0.0/15 同时也是部分代理 fake-ip 常用网段，下面会单独处理。
_BLOCKED_IP_NETWORKS = tuple(
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

# 198.18.0.0/15 是 RFC 2544 benchmarking 网段。
# Clash / sing-box / mihomo 等代理 fake-ip 模式也常用这个网段生成虚假解析结果。
# 如果系统 DNS 只返回 fake-ip，需要额外用 DoH 查询真实公网地址。
_FAKE_IP_NETWORKS = (ip_network("198.18.0.0/15"),)

_DOH_RECORD_TYPES = (
    dns.rdatatype.A,
    dns.rdatatype.AAAA,
)

_DOH_TIMEOUT_SECONDS = 5.0

# 本地域名禁止访问：
# - localhost / localhost.localdomain 明确指向本机语义。
# - *.localhost 是保留测试域名。
# - *.local 常用于 mDNS / 局域网发现，不应允许服务端主动访问。
_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}

_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
)


def _is_private_or_reserved_ip(value: str) -> bool:
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

    return any(ip in network for network in _BLOCKED_IP_NETWORKS)


def _validate_hostname(hostname: str) -> str:
    normalized = hostname.strip().strip(".").lower()

    if not normalized:
        raise UrlSecurityError("URL is missing a hostname")

    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        raise UrlSecurityError("URL hostname contains invalid characters")

    if normalized in _BLOCKED_HOSTNAMES or normalized.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UrlSecurityError("Hostname is blocked")

    return normalized


def _resolve_with_system_dns(hostname: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UrlSecurityError(f"Hostname cannot be resolved: {hostname}") from e

    return sorted({info[4][0] for info in infos})


def _resolve_with_doh(hostname: str) -> List[str]:
    for doh_url in tool_settings.WEB_ACCESS_DOH_SERVERS:
        ips: List[str] = []

        for record_type in _DOH_RECORD_TYPES:
            try:
                query = dns.message.make_query(hostname, record_type)
                response = dns.query.https(
                    query,
                    doh_url,
                    timeout=_DOH_TIMEOUT_SECONDS,
                )
            except Exception:
                log_fail(
                    "DoH 查询",
                    f"hostname={hostname}, doh={doh_url}, type={record_type}",
                )
                continue

            for rrset in response.answer:
                for item in rrset:
                    value = str(item)
                    try:
                        ip_address(value)
                    except ValueError:
                        continue

                    ips.append(value)

        if ips:
            return sorted(set(ips))

    return []


def _resolve_public_host_ips(hostname: str) -> List[str]:
    normalized = _validate_hostname(hostname)

    try:
        ip = ip_address(normalized)
    except ValueError:
        ip = None

    # URL hostname 本身就是 IP 字面量时，不走 DNS，直接做公网地址校验。
    if ip is not None:
        if _is_private_or_reserved_ip(str(ip)):
            raise UrlSecurityError("IP address is blocked")

        return [str(ip)]

    system_ips = _resolve_with_system_dns(normalized)
    if not system_ips:
        raise UrlSecurityError(f"Hostname did not resolve to any address: {normalized}")

    # 代理 fake-ip 场景：
    # 系统 DNS 可能返回 198.18.0.0/15 这类虚假 IP。
    # 这种 IP 不能直接判定目标真实地址，因此改用可信 DoH 服务器重新解析。
    if all(
        any(ip_address(value) in network for network in _FAKE_IP_NETWORKS)
        for value in system_ips
    ):
        trusted_ips = _resolve_with_doh(normalized)
        if not trusted_ips:
            raise UrlSecurityError(
                "Hostname resolved to fake-ip and DoH could not resolve a real "
                f"address: {normalized}"
            )

        blocked_ip = next(
            (value for value in trusted_ips if _is_private_or_reserved_ip(value)),
            "",
        )
        if blocked_ip:
            raise UrlSecurityError(
                "Hostname resolves to a blocked IP address through DoH: "
                f"{blocked_ip}"
            )

        return trusted_ips

    blocked_ip = next(
        (value for value in system_ips if _is_private_or_reserved_ip(value)),
        "",
    )
    if blocked_ip:
        raise UrlSecurityError(
            f"Hostname resolves to a blocked IP address: {blocked_ip}"
        )

    return system_ips


def validate_public_http_url(url: str) -> str:
    if not url:
        raise UrlSecurityError("URL is empty")

    if url != url.strip() or any(
        ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url
    ):
        raise UrlSecurityError("URL contains invalid characters")

    if "\\" in url:
        raise UrlSecurityError("URL cannot contain backslashes")

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise UrlSecurityError("URL scheme must be http or https")

    if not parsed.netloc or not parsed.hostname:
        raise UrlSecurityError("URL is missing a hostname")

    if parsed.username or parsed.password:
        raise UrlSecurityError("URL cannot contain userinfo")

    try:
        port = parsed.port
    except ValueError as e:
        raise UrlSecurityError("URL port is invalid") from e

    if port is not None:
        if port <= 0 or port > 65535:
            raise UrlSecurityError("URL port is invalid")

        # 服务端主动访问只允许标准 HTTP/HTTPS 端口，降低 SSRF 打内网服务的风险。
        if port not in {80, 443}:
            raise UrlSecurityError("URL port is not allowed")

    _resolve_public_host_ips(parsed.hostname)

    return url