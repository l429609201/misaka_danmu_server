"""
弹弹Play 兼容 API 的辅助函数

包含缓存操作和映射管理的辅助函数。
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, orm_models, CacheManager
from src.core.cache import get_cache_backend

# 同包内相对导入
from .constants import (
    EPISODE_MAPPING_CACHE_PREFIX,
    FALLBACK_SEARCH_CACHE_PREFIX,
    FALLBACK_SEARCH_CACHE_TTL,
    USER_LAST_BANGUMI_CHOICE_PREFIX,
    USER_LAST_BANGUMI_CHOICE_TTL,
)

logger = logging.getLogger(__name__)


# ==================== 缓存辅助函数 ====================

def convert_to_serializable(obj: Any) -> Any:
    """递归转换对象为可JSON序列化的格式"""
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    elif hasattr(obj, 'dict'):
        return obj.dict()
    elif isinstance(obj, dict):
        return {k: convert_to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(item) for item in obj]
    else:
        return obj


async def get_db_cache(session: AsyncSession, prefix: str, key: str) -> Optional[Any]:
    """从缓存中获取数据（优先走缓存后端，回退到数据库直接查询）"""
    backend = get_cache_backend()
    cache_key = f"{prefix}{key}"

    if backend is not None:
        try:
            return await backend.get(cache_key, region="default")
        except Exception as e:
            logger.warning(f"缓存后端读取失败，回退到数据库: {cache_key}, 错误: {e}")

    # 回退：直接走数据库
    cached_data = await crud.get_cache(session, cache_key)
    if cached_data:
        if not isinstance(cached_data, str):
            return cached_data
        if not cached_data.strip():
            return None
        try:
            return json.loads(cached_data)
        except (json.JSONDecodeError, TypeError):
            return cached_data
    return None


async def set_db_cache(session: AsyncSession, prefix: str, key: str, value: Any, ttl_seconds: int):
    """设置缓存（优先走缓存后端，回退到数据库直接写入）"""
    backend = get_cache_backend()
    cache_key = f"{prefix}{key}"

    # 先将 Pydantic model 等转为可序列化格式
    if not isinstance(value, (str, int, float, bool, type(None))):
        value = convert_to_serializable(value)

    if backend is not None:
        try:
            await backend.set(cache_key, value, ttl=ttl_seconds, region="default")
            logger.debug(f"设置缓存: {cache_key}")
            return
        except Exception as e:
            logger.warning(f"缓存后端写入失败，回退到数据库: {cache_key}, 错误: {e}")

    # 回退：直接走数据库
    try:
        if isinstance(value, str):
            json_value = value
        else:
            json_value = json.dumps(value, ensure_ascii=False)
        await crud.set_cache(session, cache_key, json_value, ttl_seconds)
        logger.debug(f"设置缓存(数据库回退): {cache_key}")
    except Exception as e:
        logger.error(f"设置缓存失败: {cache_key}, 错误: {e}")


async def delete_db_cache(session: AsyncSession, prefix: str, key: str) -> bool:
    """删除缓存（优先走缓存后端，回退到数据库直接删除）"""
    backend = get_cache_backend()
    cache_key = f"{prefix}{key}"

    if backend is not None:
        try:
            result = await backend.delete(cache_key, region="default")
            if result:
                logger.debug(f"删除缓存: {cache_key}")
            return result
        except Exception as e:
            logger.warning(f"缓存后端删除失败，回退到数据库: {cache_key}, 错误: {e}")

    # 回退：直接走数据库
    try:
        result = await crud.delete_cache(session, cache_key)
        if result:
            logger.debug(f"删除缓存(数据库回退): {cache_key}")
        return result
    except Exception as e:
        logger.error(f"删除缓存失败: {cache_key}, 错误: {e}")
        return False


async def get_cache_keys(session: AsyncSession, pattern: str) -> List[str]:
    """按模式获取缓存键列表（优先走缓存后端，回退到数据库）"""
    backend = get_cache_backend()

    if backend is not None:
        try:
            return await backend.keys(pattern, region="default")
        except Exception as e:
            logger.warning(f"缓存后端 keys 失败，回退到数据库: {pattern}, 错误: {e}")

    # 回退：直接走数据库
    return await crud.get_cache_keys_by_pattern(session, pattern)


# ==================== 映射辅助函数 ====================

async def store_episode_mapping(
    session: AsyncSession, episode_id: int, provider: str,
    media_id: str, episode_index: int, original_title: str
):
    """存储episodeId到源的映射关系到缓存"""
    mapping_data = {
        "provider": provider, "media_id": media_id,
        "episode_index": episode_index, "original_title": original_title,
        "timestamp": time.time()
    }
    await set_db_cache(session, EPISODE_MAPPING_CACHE_PREFIX, str(episode_id), mapping_data, 10800)
    logger.debug(f"存储episodeId映射: {episode_id} -> {provider}:{media_id}")


async def get_episode_mapping(session: AsyncSession, episode_id: int) -> Optional[Dict[str, Any]]:
    """从缓存中获取episodeId的映射关系"""
    mapping_data = await get_db_cache(session, EPISODE_MAPPING_CACHE_PREFIX, str(episode_id))
    if mapping_data:
        try:
            if isinstance(mapping_data, str):
                mapping_data = json.loads(mapping_data)
            logger.info(f"从缓存获取episodeId映射: {episode_id} -> {mapping_data['provider']}:{mapping_data['media_id']}")
            return mapping_data
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"解析episodeId映射缓存失败: {e}")
    return None


def format_episode_ranges(episodes: List[int]) -> str:
    """将分集列表格式化为简洁的范围表示 — 委托给统一模块"""
    from src.utils.filename_parser import format_episode_ranges as _fmt
    return _fmt(episodes, separator=",")


async def find_existing_anime_by_bangumi_id(
    session: AsyncSession, bangumi_id: str, search_key: str
) -> Optional[Dict[str, Any]]:
    """根据bangumiId和搜索会话查找已存在的映射记录，返回anime信息"""
    search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
    if search_info and "bangumi_mapping" in search_info:
        if bangumi_id in search_info["bangumi_mapping"]:
            mapping_info = search_info["bangumi_mapping"][bangumi_id]
            if mapping_info.get("real_anime_id"):
                real_anime_id = mapping_info["real_anime_id"]
                title = mapping_info.get("original_title", "未知")
                logger.debug(f"在当前搜索会话中找到已存在的剧集: bangumiId={bangumi_id}, title='{title}' (anime_id={real_anime_id})")
                return {"animeId": real_anime_id, "title": title}
    logger.debug(f"在当前搜索会话中未找到已存在的剧集: bangumiId={bangumi_id}")
    return None


async def update_episode_mapping(
    session: AsyncSession, episode_id: int, provider: str,
    media_id: str, episode_index: int, original_title: str
):
    """更新episodeId的映射关系（更新数据库缓存）"""
    await store_episode_mapping(session, episode_id, provider, media_id, episode_index, original_title)
    real_anime_id = int(str(episode_id)[2:8])
    try:
        all_cache_keys = await get_cache_keys(session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
        for cache_key in all_cache_keys:
            search_key = cache_key.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
            search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
            if search_info and search_info.get("status") == "completed" and "bangumi_mapping" in search_info:
                for bangumi_id, mapping_info in search_info["bangumi_mapping"].items():
                    if mapping_info.get("real_anime_id") == real_anime_id:
                        mapping_info["provider"] = provider
                        mapping_info["media_id"] = media_id
                        await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info, FALLBACK_SEARCH_CACHE_TTL)
                        await set_db_cache(session, USER_LAST_BANGUMI_CHOICE_PREFIX, search_key, bangumi_id, USER_LAST_BANGUMI_CHOICE_TTL)
                        logger.info(f"更新数据库缓存映射: real_anime_id={real_anime_id}, provider={provider}")
                        break
    except Exception as e:
        logger.warning(f"更新缓存映射失败: {e}")
    logger.debug(f"更新episodeId映射: {episode_id} -> {provider}:{media_id}")


async def check_related_match_fallback_task(session: AsyncSession, search_term: str) -> Optional[Dict[str, Any]]:
    """检查是否有相关的后备匹配任务正在进行，返回任务信息或None"""
    stmt = select(orm_models.TaskStateCache).where(
        orm_models.TaskStateCache.taskType == "match_fallback"
    ).order_by(orm_models.TaskStateCache.createdAt.desc()).limit(10)
    result = await session.execute(stmt)
    task_caches = result.scalars().all()

    for task_cache in task_caches:
        history_stmt = select(orm_models.TaskHistory).where(
            orm_models.TaskHistory.taskId == task_cache.taskId,
            orm_models.TaskHistory.status.in_(['排队中', '运行中'])
        )
        history_result = await session.execute(history_stmt)
        task_history = history_result.scalar_one_or_none()
        if task_history:
            if search_term.lower() in task_history.title.lower() or \
               (task_history.description and search_term.lower() in task_history.description.lower()):
                return {
                    "task_id": task_history.taskId, "title": task_history.title,
                    "progress": task_history.progress or 0, "status": task_history.status,
                    "description": task_history.description or "匹配后备正在进行"
                }
    return None


async def get_next_virtual_anime_id(session: AsyncSession) -> int:
    """获取下一个虚拟animeId（6位数字，从900000开始）"""
    max_id = None
    try:
        all_cache_keys = await get_cache_keys(session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
        for cache_key in all_cache_keys:
            search_key = cache_key.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
            search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
            if search_info and search_info.get("status") == "completed" and "bangumi_mapping" in search_info:
                for bangumi_id, mapping_info in search_info["bangumi_mapping"].items():
                    anime_id = mapping_info.get("anime_id")
                    if anime_id and 900000 <= anime_id <= 999999:
                        if max_id is None or anime_id > max_id:
                            max_id = anime_id
    except Exception as e:
        logger.error(f"查找最大虚拟anime_id失败: {e}")
    return 900000 if max_id is None else max_id + 1


async def get_next_real_anime_id(session: AsyncSession) -> int:
    """获取下一个真实的animeId（当前最大animeId + 1）"""
    result = await session.execute(select(func.max(orm_models.Anime.id)))
    max_id = result.scalar()
    return 1 if max_id is None else max_id + 1

