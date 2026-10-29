"""搜索领域的缓存寿命策略，不向基础配置服务引入业务规则。"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def read_search_cache_ttl(config_service: Any) -> int:
    """读取搜索结果缓存寿命，非法值回退且保证至少三小时。"""
    minimum_ttl = 10800
    try:
        value = await config_service.get("searchTtlSeconds", minimum_ttl)
        return max(int(str(value).strip()), minimum_ttl)
    except (RuntimeError, TypeError, ValueError) as exc:
        logger.warning("读取搜索缓存 TTL 失败，使用 %d 秒: %s", minimum_ttl, exc)
        return minimum_ttl
