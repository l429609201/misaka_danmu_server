"""网络地址的纯函数工具。"""

import ipaddress


def normalize_ip(ip_str: str) -> str:
    """将 IPv4-mapped IPv6 地址还原为纯 IPv4。"""
    try:
        addr = ipaddress.ip_address(ip_str)
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
    except ValueError:
        pass
    return ip_str
