"""外链地址纯格式处理，不读取配置或探测网络。"""

from typing import Optional
from urllib.parse import urlparse


def validate_custom_domain_format(raw_domain: str) -> Optional[str]:
    """规范化 HTTP(S) 外链，拒绝缺失主机、无效地址与嵌入凭据。"""
    domain = str(raw_domain or "").strip().rstrip("/")
    try:
        parsed = urlparse(domain)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return None
        # 对外分享不能暴露 URL 中的认证信息。
        if parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    return domain
