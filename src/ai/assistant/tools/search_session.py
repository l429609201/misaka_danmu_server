"""
御坂助手 · 搜索会话缓存（三段式导入流程用）
------------------------------------------------------------
支撑「搜索 → 选择 → 编辑/直接导入」的多轮交互：
search_media 工具把候选结果按 searchId 存入缓存，
后续 get_provider_episodes / import_selected / import_edited
用 (searchId, resultIndex) 取回对应的 ProviderSearchInfo，避免把
整条搜索结果塞进对话上下文（省 token、防串改）。

复用项目统一缓存后端（get_cache_backend），失败回退到 DB 缓存表，
与 control API 的 control_search_* 缓存机制保持一致。TTL 默认 10 分钟。
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from src.db import crud, models
from src.core.cache import get_cache_backend

logger = logging.getLogger(__name__)

# 缓存键前缀（与 control API 的 control_search_ 区分，避免互相污染）
_CACHE_PREFIX = "assistant_search_"
# 搜索会话缓存有效期（秒），与 control API 的 /search 一致
_CACHE_TTL = 600


def _cache_key(search_id: str) -> str:
    return f"{_CACHE_PREFIX}{search_id}"


async def save_search_results(
    session, results: List[models.ProviderSearchInfo]
) -> str:
    """把搜索结果存入缓存，返回新生成的 searchId。

    优先写统一缓存后端（region=default），异常时回退 DB 缓存表。
    存储内容为各结果的 model_dump()，与 control API 保持一致。
    """
    search_id = str(uuid.uuid4())
    key = _cache_key(search_id)
    payload = [r.model_dump() for r in results]

    backend = get_cache_backend()
    if backend is not None:
        try:
            await backend.set(key, payload, ttl=_CACHE_TTL, region="default")
            return search_id
        except Exception as e:  # noqa: BLE001
            logger.warning(f"御坂助手搜索缓存写入后端失败，回退DB: {e}")
    await crud.set_cache(session, key, payload, _CACHE_TTL)
    return search_id


async def load_search_results(
    session, search_id: str
) -> Optional[List[models.ProviderSearchInfo]]:
    """按 searchId 取回搜索结果列表；不存在或已过期返回 None。"""
    if not search_id:
        return None
    key = _cache_key(search_id)
    raw = None
    backend = get_cache_backend()
    if backend is not None:
        try:
            raw = await backend.get(key, region="default")
        except Exception:
            raw = None
    if raw is None:
        raw = await crud.get_cache(session, key)
    if raw is None:
        return None
    try:
        return [models.ProviderSearchInfo.model_validate(r) for r in raw]
    except Exception as e:  # noqa: BLE001
        logger.error(f"御坂助手搜索缓存解析失败 searchId={search_id}: {e}")
        return None


async def get_result_item(
    session, search_id: str, result_index: int
) -> Tuple[Optional[models.ProviderSearchInfo], Optional[str]]:
    """按 (searchId, resultIndex) 取回单个候选项。

    返回 (item, error)：命中返回 (item, None)；失败返回 (None, 错误说明)。
    错误说明为面向用户的中文，供工具直接回灌给模型。
    """
    results = await load_search_results(session, search_id)
    if results is None:
        return None, "搜索会话已过期或无效，请让用户重新发起搜索（调用 search_media）。"
    if not isinstance(result_index, int) or not (0 <= result_index < len(results)):
        return None, f"resultIndex 无效（应在 0~{len(results) - 1} 之间）。"
    return results[result_index], None
