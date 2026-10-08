"""
弹弹Play 兼容 API 的辅助函数

包含缓存操作和映射管理的辅助函数。
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.service_container import get_database_service
# 缓存统一经服务协调，数据库回退仍复用调用方事务。
from src.services.cache_service import get_cache_service

# 常量已迁移到纯工具层，避免依赖不存在的同包模块。
from src.utils.dandan.constants import (
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
    cache_service = get_cache_service()
    cache_key = f"{prefix}{key}"

    if cache_service is not None:
        try:
            result = await cache_service.get(cache_key, region="default")
            if isinstance(result, str):
                if not result.strip():
                    return None
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    return result
            return _fix_bangumi_mapping(result)
        except Exception as e:
            logger.warning(f"缓存后端读取失败，回退到数据库: {cache_key}, 错误: {e}")

    # 回退：直接走数据库
    db = get_database_service()
    async with db.transaction(session=session):
        cached_data = await db.cache.get_cache(cache_key)
    if cached_data:
        if not isinstance(cached_data, str):
            return _fix_bangumi_mapping(cached_data)
        if not cached_data.strip():
            return None
        try:
            return _fix_bangumi_mapping(json.loads(cached_data))
        except (json.JSONDecodeError, TypeError):
            return cached_data
    return None


def _fix_bangumi_mapping(data: Any) -> Any:
    """
    修复缓存中 bangumi_mapping 的 value 是 JSON 字符串（double-serialized）的情况。
    如果 bangumi_mapping 某个 value 是 str，尝试 json.loads 转换为 dict。
    """
    if not isinstance(data, dict):
        return data
    mapping = data.get("bangumi_mapping")
    if not isinstance(mapping, dict):
        return data
    fixed = False
    for bid, mi in mapping.items():
        if isinstance(mi, str):
            try:
                mapping[bid] = json.loads(mi)
                fixed = True
            except (json.JSONDecodeError, TypeError):
                pass  # 无法修复的保持原样，调用方再容错
    if fixed:
        data["bangumi_mapping"] = mapping
    return data


async def set_db_cache(session: AsyncSession, prefix: str, key: str, value: Any, ttl_seconds: int) -> None:
    """设置缓存，服务不可用时借用调用方会话写入数据库。"""
    cache_service = get_cache_service()
    cache_key = f"{prefix}{key}"

    # 先将 Pydantic model 等转为可序列化格式
    if not isinstance(value, (str, int, float, bool, type(None))):
        value = convert_to_serializable(value)

    if cache_service is not None:
        try:
            await cache_service.set(cache_key, value, ttl=ttl_seconds, region="default")
            logger.debug(f"设置缓存: {cache_key}")
            return
        except Exception as e:
            logger.warning(f"缓存服务写入失败，回退到数据库: {cache_key}, 错误: {e}")

    try:
        json_value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        db = get_database_service()
        async with db.transaction(session=session):
            await db.cache.set_cache(cache_key, json_value, ttl_seconds)
        logger.debug(f"设置缓存(数据库回退): {cache_key}")
    except Exception as e:
        logger.error(f"设置缓存失败: {cache_key}, 错误: {e}")


async def delete_db_cache(session: AsyncSession, prefix: str, key: str) -> bool:
    """删除缓存，服务不可用时借用调用方会话删除数据库缓存。"""
    cache_service = get_cache_service()
    cache_key = f"{prefix}{key}"

    if cache_service is not None:
        try:
            result = await cache_service.delete(cache_key, region="default")
            if result:
                logger.debug(f"删除缓存: {cache_key}")
            return result
        except Exception as e:
            logger.warning(f"缓存服务删除失败，回退到数据库: {cache_key}, 错误: {e}")

    try:
        db = get_database_service()
        async with db.transaction(session=session):
            result = await db.cache.delete_cache(cache_key)
        if result:
            logger.debug(f"删除缓存(数据库回退): {cache_key}")
        return result
    except Exception as e:
        logger.error(f"删除缓存失败: {cache_key}, 错误: {e}")
        return False


async def get_cache_keys(session: AsyncSession, pattern: str) -> List[str]:
    """按模式获取缓存键，服务不可用时借用调用方会话查询。"""
    cache_service = get_cache_service()

    if cache_service is not None:
        try:
            return await cache_service.keys(pattern, region="default")
        except Exception as e:
            logger.warning(f"缓存服务查询键失败，回退到数据库: {pattern}, 错误: {e}")

    db = get_database_service()
    async with db.transaction(session=session):
        return await db.cache.get_cache_keys_by_pattern(pattern)


# ==================== 映射辅助函数 ====================

async def store_episode_mapping(
    session: AsyncSession, episode_id: int, provider: str,
    media_id: str, episode_index: int, original_title: str,
    season: int = 1
):
    """存储episodeId到源的映射关系到缓存"""
    mapping_data = {
        "provider": provider, "media_id": media_id,
        "episode_index": episode_index, "original_title": original_title,
        "season": season,
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


async def update_episode_mappings(
    episode_indices: Dict[int, int], provider: str, media_id: str,
    original_title: str, season: int = 1,
    extra_entries: Optional[Dict[str, tuple[Any, int]]] = None,
) -> None:
    """一次更新全部分集映射，旧搜索缓存只扫描一次，每个缓存只写一次。"""
    if not episode_indices:
        return
    real_ids = {int(str(episode_id)[2:8]) for episode_id in episode_indices}
    if len(real_ids) != 1:
        raise ValueError("一次切源只能更新同一作品的分集映射")
    real_anime_id = real_ids.pop()
    timestamp = time.time()
    entries = {
        f"{EPISODE_MAPPING_CACHE_PREFIX}{episode_id}": ({
            "provider": provider, "media_id": media_id,
            "episode_index": index, "original_title": original_title,
            "season": season, "timestamp": timestamp,
        }, 10800)
        for episode_id, index in episode_indices.items()
    }
    service = get_cache_service()
    db = get_database_service()
    # 仅兼容旧下划线命名的搜索会话；新版候选源身份不得被全局覆盖。
    if service is not None:
        caches = await service.get_by_prefix(FALLBACK_SEARCH_CACHE_PREFIX, region="default")
    else:
        async with db.transaction():
            caches = await db.cache.get_json_by_prefix(FALLBACK_SEARCH_CACHE_PREFIX)
    updated = 0
    for key, info in caches.items():
        if isinstance(info, str):
            try:
                info = json.loads(info)
            except (ValueError, TypeError):
                continue
        info = _fix_bangumi_mapping(info)
        if not isinstance(info, dict) or info.get("status") != "completed":
            continue
        mappings = info.get("bangumi_mapping")
        if not isinstance(mappings, dict):
            continue
        for bangumi_id, mapping in mappings.items():
            if not isinstance(mapping, dict) or mapping.get("real_anime_id") != real_anime_id:
                continue
            mapping.update(provider=provider, media_id=media_id)
            entries[key] = (info, FALLBACK_SEARCH_CACHE_TTL)
            search_key = key[len(FALLBACK_SEARCH_CACHE_PREFIX):]
            entries[f"{USER_LAST_BANGUMI_CHOICE_PREFIX}{search_key}"] = (
                bangumi_id, USER_LAST_BANGUMI_CHOICE_TTL
            )
            updated += 1
            break
    entries.update(extra_entries or {})
    # 远端驱动实现真正批量写入，不使用逐集 await 或并发单条 SQL 伪装批量。
    if service is not None:
        await service.set_many(entries, region="default")
    else:
        async with db.transaction():
            await db.cache.set_json_many(entries)
    logger.info(
        "批量更新缓存映射: real_anime_id=%s, provider=%s, 分集数=%d, 搜索缓存数=%d",
        real_anime_id, provider, len(episode_indices), updated,
    )


async def check_related_match_fallback_task(session: AsyncSession, search_term: str) -> Optional[Dict[str, Any]]:
    """检查是否有相关的后备匹配任务正在进行，返回任务信息或None

    已迁移至 FallbackRepository.check_related_match_fallback_task()，
    此处委托调用，保留 session 参数兼容现有调用方。
    """
    db = get_database_service()
    async with db.transaction():
        return await db.fallback.check_related_match_fallback_task(search_term)


async def get_next_virtual_anime_id(session: AsyncSession) -> int:
    """获取下一个虚拟animeId（6位数字，从900000开始）"""
    max_id = None
    try:
        all_cache_keys = await get_cache_keys(session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
        for cache_key in all_cache_keys:
            search_key = cache_key.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
            search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
            if not isinstance(search_info, dict):
                continue
            if search_info.get("status") == "completed" and "bangumi_mapping" in search_info:
                for bangumi_id, mapping_info in search_info["bangumi_mapping"].items():
                    anime_id = mapping_info.get("anime_id")
                    if anime_id and 900000 <= anime_id <= 999999:
                        if max_id is None or anime_id > max_id:
                            max_id = anime_id
    except Exception as e:
        logger.error(f"查找最大虚拟anime_id失败: {e}")
    return 900000 if max_id is None else max_id + 1


async def get_next_real_anime_id(session: AsyncSession) -> int:
    """获取下一个真实的 animeId，保证不重用已删除的 id。

    已迁移至 FallbackRepository.get_next_real_anime_id()，
    此处委托调用，保留 session 参数兼容现有调用方。
    """
    db = get_database_service()
    async with db.transaction():
        return await db.fallback.get_next_real_anime_id()

