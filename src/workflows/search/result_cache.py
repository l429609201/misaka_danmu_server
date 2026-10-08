"""搜索结果缓存编排：复用统一缓存能力，默认查询全部结果。"""
import logging
from copy import deepcopy
from typing import Any, Callable, Optional

from src.services.cache_service import get_cache_service

logger = logging.getLogger(__name__)
ResultFilter = Callable[[dict[str, Any]], bool]


def select_search_results(
    results: list[dict[str, Any]], *, predicate: Optional[ResultFilter] = None,
    page: Optional[int] = None, page_size: Optional[int] = None,
) -> dict[str, Any]:
    """先筛选后可选分页；未传分页返回全部，响应副本不污染缓存。"""
    if (page is None) != (page_size is None):
        raise ValueError("分页时必须同时指定 page 和 page_size")
    if page is not None and (page < 1 or page_size < 1):
        raise ValueError("页码和每页数量必须大于零")
    filtered = [item for item in results if predicate is None or predicate(deepcopy(item))]
    total = len(filtered)
    if page is not None:
        start = (page - 1) * page_size
        filtered = filtered[start:start + page_size]
    return {"results": deepcopy(filtered), "total": total}


async def read_search_results(
    key: str, *, region: str = "search", predicate: Optional[ResultFilter] = None,
    page: Optional[int] = None, page_size: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """读取列表或带上下文的全量结果；None 表示未命中，空列表仍算命中。"""
    # 在读取前校验参数，避免分页错误因是否命中而表现不同。
    select_search_results([], page=page, page_size=page_size)
    service = get_cache_service()
    if service is None:
        logger.warning("搜索缓存服务未初始化: %s:%s", region, key)
        return None
    try:
        value = deepcopy(await service.get(key, region=region))
    except Exception:
        logger.warning("搜索缓存读取失败: %s:%s", region, key, exc_info=True)
        return None
    if value is None:
        logger.info("搜索结果缓存未命中: %s:%s", region, key)
        return None
    payload = {"results": value} if isinstance(value, list) else value
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        logger.warning("搜索缓存结果格式无效: %s:%s", region, key)
        return None
    if not all(isinstance(item, dict) for item in payload["results"]):
        logger.warning("搜索缓存结果条目格式无效: %s:%s", region, key)
        return None
    logger.info("搜索结果缓存命中: %s:%s，共%s条", region, key, len(payload["results"]))
    return {**payload, **select_search_results(
        payload["results"], predicate=predicate, page=page, page_size=page_size,
    )}


async def write_search_results(
    key: str, results: list[dict[str, Any]], *, ttl: int,
    region: str = "search", context: Optional[dict[str, Any]] = None,
) -> bool:
    """保存调用方提供的全量结果；无分页参数，保留既有键、区域和寿命。"""
    service = get_cache_service()
    if service is None:
        logger.warning("搜索缓存服务未初始化，无法写入: %s:%s", region, key)
        return False
    # 不带上下文时保持既有列表存储格式，兼容控制 API 的搜索会话。
    value = results if context is None else {**context, "results": results}
    try:
        await service.set(key, deepcopy(value), ttl=ttl, region=region)
        return True
    except Exception:
        logger.warning("搜索缓存写入失败: %s:%s", region, key, exc_info=True)
        return False
