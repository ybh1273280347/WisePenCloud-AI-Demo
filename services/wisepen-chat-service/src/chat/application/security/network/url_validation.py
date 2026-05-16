import socket
from ipaddress import ip_address, ip_network
from typing import List
from urllib.parse import urlparse

import dns.message
import dns.query
import dns.rdatatype
from chat.core.config.app_settings import settings
from common.logger import log_fail

from .errors import UrlSecurityError

DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".docm",
    ".pptx",
    ".pptm",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".ods",
    ".epub",
)

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

_FAKE_IP_NETWORKS = (ip_network("198.18.0.0/15"),)

_DOH_RECORD_TYPES = (
    dns.rdatatype.A,
    dns.rdatatype.AAAA,
)

_DOH_TIMEOUT_SECONDS = 5.0

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}

_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
)


def _normalize_hostname(hostname: str) -> str:
    return hostname.strip().strip(".").lower()


def _is_blocked_hostname(hostname: str) -> bool:
    normalized = _normalize_hostname(hostname)
    if normalized in _BLOCKED_HOSTNAMES:
        return True

    return normalized.endswith(_BLOCKED_HOST_SUFFIXES)


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


def _is_fake_ip(value: str) -> bool:
    try:
        ip = ip_address(value)
    except ValueError:
        return False

    return any(ip in network for network in _FAKE_IP_NETWORKS)


def _has_invalid_hostname_chars(hostname: str) -> bool:
    return any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in hostname)


def _is_ip_literal(value: str) -> bool:
    try:
        ip_address(value)
        return True
    except ValueError:
        return False


def _find_blocked_ip(ips: List[str]) -> str:
    for ip in ips:
        if _is_private_or_reserved_ip(ip):
            return ip

    return ""


def _all_fake_ips(ips: List[str]) -> bool:
    return bool(ips) and all(_is_fake_ip(ip) for ip in ips)


def _resolve_with_system_dns(hostname: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UrlSecurityError(f"Hostname cannot be resolved: {hostname}") from e

    return sorted({info[4][0] for info in infos})


def _extract_ips_from_doh_answer(response) -> List[str]:
    ips: List[str] = []

    for rrset in response.answer:
        for item in rrset:
            ip = str(item)
            if _is_ip_literal(ip):
                ips.append(ip)

    return ips


def _query_doh_record(hostname: str, doh_url: str, record_type) -> List[str]:
    query = dns.message.make_query(hostname, record_type)
    response = dns.query.https(query, doh_url, timeout=_DOH_TIMEOUT_SECONDS)
    return _extract_ips_from_doh_answer(response)


def _resolve_with_doh_server(hostname: str, doh_url: str) -> List[str]:
    ips: List[str] = []

    for record_type in _DOH_RECORD_TYPES:
        try:
            ips.extend(_query_doh_record(hostname, doh_url, record_type))
        except Exception:
            log_fail(
                "DoH 查询", f"hostname={hostname}, doh={doh_url}, type={record_type}"
            )
            continue

    return sorted(set(ips))


def _resolve_with_doh(hostname: str) -> List[str]:
    for doh_url in settings.WEB_ACCESS_DOH_SERVERS:
        ips = _resolve_with_doh_server(hostname, doh_url)
        if ips:
            return ips

    return []


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


def _validate_hostname(hostname: str) -> str:
    normalized = _normalize_hostname(hostname)

    if not normalized:
        raise UrlSecurityError("URL is missing a hostname")

    if _has_invalid_hostname_chars(normalized):
        raise UrlSecurityError("URL hostname contains invalid characters")

    if _is_blocked_hostname(normalized):
        raise UrlSecurityError("Hostname is blocked")

    return normalized


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
        raise UrlSecurityError(
            f"Hostname resolves to a blocked IP address: {blocked_ip}"
        )

    return system_ips


def is_public_http_url(url: str) -> bool:
    try:
        validate_public_http_url(url)
        return True
    except UrlSecurityError:
        return False


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

        if port not in {80, 443}:
            raise UrlSecurityError("URL port is not allowed")

    _resolve_public_host_ips(parsed.hostname)

    return url
