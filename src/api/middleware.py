"""
网络工具模块

包含：
- normalize_ip: IPv4-mapped IPv6 地址标准化（公共工具函数）

注：原 capture_api_response / log_not_found_requests 已重构为纯 ASGI 中间件，
迁移至 src/utils/asgi_middleware.py（规避 BaseHTTPMiddleware 客户端断开时级联取消 DB 操作）。
"""

import logging
import ipaddress


logger = logging.getLogger(__name__)


def normalize_ip(ip_str: str) -> str:
    """标准化 IP 地址：将 IPv4-mapped IPv6（::ffff:x.x.x.x）还原为纯 IPv4"""
    try:
        addr = ipaddress.ip_address(ip_str)
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
    except ValueError:
        pass
    return ip_str

