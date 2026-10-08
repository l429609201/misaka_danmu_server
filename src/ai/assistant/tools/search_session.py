"""
御坂助手 · 搜索会话缓存（三段式导入流程用）
------------------------------------------------------------
支撑「搜索 → 选择 → 编辑/直接导入」的多轮交互：
search_media 工具把候选结果按 searchId 存入缓存，
后续 get_provider_episodes / import_selected / import_edited
用 (searchId, resultIndex) 取回对应的 ProviderSearchInfo，避免把
整条搜索结果塞进对话上下文（省 token、防串改）。

统一走 CacheService（get_cache_service）读写：分层存储、落库与容错
均由缓存服务层内部决定，本模块只负责 get/set，不关心底层如何存储。
与 control API 的 control_search_* 缓存机制保持一致。TTL 默认 10 分钟。
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from src.schemas import ProviderSearchInfo
from src.services.cache_service import get_cache_service

logger = logging.getLogger(__name__)

# 缓存键前缀（与 control API 的 control_search_ 区分，避免互相污染）
_CACHE_PREFIX = "assistant_search_"
# 搜索会话缓存有效期（秒），与 control API 的 /search 一致
_CACHE_TTL = 600


def _cache_key(search_id: str) -> str:
    return f"{_CACHE_PREFIX}{search_id}"


async def save_search_results(
    results: List[ProviderSearchInfo]
) -> str:
    """把搜索结果存入缓存，返回新生成的 searchId。

    存储内容为各结果的 model_dump()，与 control API 保持一致。
    分层存储与落库策略由 CacheService 内部决定，此处只负责写入。
    """


    search_id = str(uuid.uuid4())
    key = _cache_key(search_id)
    payload = [r.model_dump() for r in results]

    await get_cache_service().set(key, payload, ttl=_CACHE_TTL, region="default")
    return search_id


async def load_search_results(
    search_id: str
) -> Optional[List[ProviderSearchInfo]]:
    """按 searchId 取回搜索结果列表；不存在或已过期返回 None。

    读取路径（内存/数据库分层）由 CacheService 内部决定。
    """

    if not search_id:
        return None
    raw = await get_cache_service().get(_cache_key(search_id), region="default")
    if raw is None:
        return None
    try:
        return [ProviderSearchInfo.model_validate(r) for r in raw]
    except Exception as e:  # noqa: BLE001
        logger.error(f"御坂助手搜索缓存解析失败 searchId={search_id}: {e}")
        return None


async def get_result_item(
    search_id: str, result_index: int
) -> Tuple[Optional[ProviderSearchInfo], Optional[str]]:
    """按 (searchId, resultIndex) 取回单个候选项。

    返回 (item, error)：命中返回 (item, None)；失败返回 (None, 错误说明)。
    错误说明为面向用户的中文，供工具直接回灌给模型。
    """
    results = await load_search_results(search_id)
    if results is None:
        return None, "搜索会话已过期或无效，请让用户重新发起搜索（调用 search_media）。"
    if not isinstance(result_index, int) or not (0 <= result_index < len(results)):
        return None, f"resultIndex 无效（应在 0~{len(results) - 1} 之间）。"
    return results[result_index], None
