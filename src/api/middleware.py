"""
网络工具模块

包含：
- normalize_ip: IPv4-mapped IPv6 地址标准化（公共工具函数）

注：原 capture_api_response / log_not_found_requests 已重构为纯 ASGI 中间件，
迁移至 src/utils/asgi_middleware.py（规避 BaseHTTPMiddleware 客户端断开时级联取消 DB 操作）。
"""

import logging

from src.utils.runtime.ip_address import normalize_ip

logger = logging.getLogger(__name__)

