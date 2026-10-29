"""客户端地址及网段匹配的纯函数，不读取配置或访问数据库。"""

import ipaddress
from typing import Mapping

from src.utils.runtime.ip_address import normalize_ip


def resolve_client_ip(peer_ip: str, headers: Mapping[str, str], trusted_proxies: str) -> str:
    """仅当直连地址属于可信代理时读取转发头。"""
    client_ip = normalize_ip(peer_ip)
    try:
        peer = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip
    trusted = False
    for entry in trusted_proxies.split(","):
        if not entry.strip():
            continue
        try:
            if peer in ipaddress.ip_network(entry.strip(), strict=False):
                trusted = True
                break
        except ValueError:
            continue
    if trusted:
        forwarded = headers.get("x-forwarded-for", "")
        candidate = forwarded.split(",", 1)[0].strip() if forwarded else headers.get("x-real-ip", client_ip)
        try:
            return str(ipaddress.ip_address(normalize_ip(candidate.strip())))
        except ValueError:
            # 无效转发头不改变地址，不能借此绕过白名单。
            return client_ip
    return client_ip


def is_ip_whitelisted(client_ip: str, whitelist: str) -> bool:
    """匹配白名单，统一拒绝覆盖范围过大的危险网段。"""
    try:
        address = ipaddress.ip_address(normalize_ip(client_ip))
    except ValueError:
        return False
    for entry in whitelist.split(","):
        if not entry.strip():
            continue
        try:
            network = ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError:
            continue
        if network.prefixlen < (8 if network.version == 4 else 32):
            continue
        if address in network:
            return True
    return False
