"""
DNS 诊断脚本：检测系统 DNS 是否使用 fake-ip

fake-ip range: 198.18.0.0/15 (RFC 2544 benchmark 地址段)
常被 Clash/Mihomo/sing-box/Surge 等代理用作 fake-ip
"""

import socket
import subprocess
import ipaddress
from typing import List, Tuple

FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")

TEST_DOMAINS = [
    "docs.python.org",
    "api.github.com",
    "raw.githubusercontent.com",
    "httpbin.org",
]


def is_fake_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in FAKE_IP_RANGE
    except ValueError:
        return False


def check_socket_getaddrinfo(domain: str, port: int = 443) -> List[Tuple[str, bool]]:
    """使用 socket.getaddrinfo 解析域名"""
    results = []
    try:
        addr_infos = socket.getaddrinfo(domain, port, socket.AF_INET, socket.SOCK_STREAM)
        for af, socktype, proto, canonname, sa in addr_infos:
            ip = sa[0]
            results.append((ip, is_fake_ip(ip)))
    except Exception as e:
        results.append((f"ERROR: {e}", False))
    return results


def check_nslookup(domain: str, dns_server: str = None) -> List[str]:
    """使用 nslookup 解析域名"""
    cmd = ["nslookup", domain]
    if dns_server:
        cmd.append(dns_server)
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines = result.stdout.split("\n")
        ips = []
        in_answer = False
        for line in lines:
            if "Name:" in line and domain in line:
                in_answer = True
            if in_answer and "Address:" in line:
                parts = line.split()
                if len(parts) >= 2:
                    ip = parts[-1]
                    if ":" not in ip or ip.count(":") >= 2:
                        continue
                    ips.append(ip)
        return ips
    except Exception as e:
        return [f"ERROR: {e}"]


def main():
    print("=" * 60)
    print("DNS Fake-IP 诊断")
    print("=" * 60)
    print(f"Fake-IP Range: {FAKE_IP_RANGE}")
    print()
    
    print("-" * 60)
    print("1. socket.getaddrinfo (使用系统 DNS)")
    print("-" * 60)
    
    fake_ip_detected = False
    
    for domain in TEST_DOMAINS:
        results = check_socket_getaddrinfo(domain)
        for ip, is_fake in results:
            status = ">>> FAKE-IP <<<" if is_fake else "OK"
            if is_fake:
                fake_ip_detected = True
            print(f"  {domain}: {ip} [{status}]")
    
    print()
    print("-" * 60)
    print("2. nslookup 对比 (系统 DNS vs 公共 DNS)")
    print("-" * 60)
    
    for domain in TEST_DOMAINS:
        print(f"\n  {domain}:")
        
        print("    系统 DNS:")
        system_ips = check_nslookup(domain)
        for ip in system_ips[:3]:
            is_fake = is_fake_ip(ip)
            status = ">>> FAKE-IP <<<" if is_fake else "OK"
            if is_fake:
                fake_ip_detected = True
            print(f"      {ip} [{status}]")
        
        print("    公共 DNS (1.1.1.1):")
        public_ips = check_nslookup(domain, "1.1.1.1")
        for ip in public_ips[:3]:
            is_fake = is_fake_ip(ip)
            status = ">>> FAKE-IP <<<" if is_fake else "OK"
            print(f"      {ip} [{status}]")
    
    print()
    print("=" * 60)
    if fake_ip_detected:
        print("结论: 检测到 FAKE-IP，系统 DNS 返回 198.18.0.0/15 地址")
        print("      这表明系统正在使用 fake-ip DNS (如 Clash/Mihomo)")
    else:
        print("结论: 未检测到 FAKE-IP，系统 DNS 正常")
    print("=" * 60)


if __name__ == "__main__":
    main()
