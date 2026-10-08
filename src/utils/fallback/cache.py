"""后备流程的 3 种缓存写入（纯写，走统一缓存 src/core/cache.py）

这些函数只负责"写"后备下载产生的缓存，供后续请求快速命中：
1. 弹幕缓存        —— API 请求时快速返回，不必等任务完成
2. 整季基准缓存    —— 连续播放时，下一集请求命中此缓存触发后备下载
3. 整季匹配缓存    —— 跨流程复用，避免 /match 接口重复搜索

设计约束（方案C）：
- get_cache_service() 契约允许返回 None（core/cache.py:502）。本模块是"纯写"场景，
  backend 为 None 时采取 no-op 降级（跳过写入），不做 DB 回退——因为写缓存丢失
  仅影响"快速命中"这一优化，下次请求会重新触发下载/搜索，不影响正确性。
  （"读缓存"若丢回退会改变数据可见性，故读操作一律放 db/crud/fallback.py，不在此处。）

⚠️ 缓存键必须与现有读取端完全一致（否则写入的缓存读不到）。以下键格式
均对照 src/api/dandan/comments.py 的真实写入/读取代码核实：
- 弹幕缓存：   前缀 COMMENTS_FETCH_CACHE_PREFIX + f"comments_{episodeId}"
               → "comments_fetch_comments_{episodeId}"，TTL 300（comments.py:682）
- 整季基准：   无前缀 + f"fallback_episode_{baseId}"
               → "fallback_episode_{baseId}"，TTL 10800（comments.py:665，注意空前缀）
- 整季匹配：   前缀 FALLBACK_SEARCH_CACHE_PREFIX + f"match_season_{title}_{season}"
               → "fallback_search_match_season_{title}_{season}"，TTL 3600（comments.py:713）
"""

import json
import logging
from typing import Any, Optional

# C4 注意：避免循环导入，cache_service 改为延迟导入（在函数内部 import）

# 常量定义（原从 api.dandan.constants 导入，为避免循环依赖在此本地定义）
COMMENTS_FETCH_CACHE_PREFIX = "comments_fetch_"
COMMENTS_FETCH_CACHE_TTL = 300
FALLBACK_SEARCH_CACHE_PREFIX = "fallback_search_"

from .id_mapping import encode_series_base_id

logger = logging.getLogger(__name__)

# 缓存 region（与 helpers 旧封装一致，统一用 "default"）
_CACHE_REGION = "default"

# 整季基准缓存 TTL：3 小时（对照 comments.py:665）
SERIES_FALLBACK_CACHE_TTL = 10800
# 整季匹配缓存 TTL：1 小时（对照 comments.py:713）
MATCH_SEASON_CACHE_TTL = 3600


def _to_serializable(value: Any) -> Any:
    """将 value 转为可写入缓存的形式。

    统一缓存后端（hybrid/redis/database）在 L2 落地时需要可 JSON 序列化的数据。
    这里对 dict/list 原样返回（后端自行序列化），仅兜底处理不可直接序列化的对象。
    why: 原 set_db_cache 会对非基础类型调用 convert_to_serializable；后备这 3 种缓存
    写入的都是 dict（已是基础类型组合），无需额外转换，保持简单。
    """
    return value


async def _safe_set(key: str, value: Any, ttl: int) -> bool:
    """统一的"纯写"入口：backend 为 None 时 no-op 降级，异常不外抛。

    Returns:
        True 表示已写入；False 表示 backend 不可用或写入失败（已降级/记日志）。
    """
    # C4 注意：避免循环导入，cache_service 延迟导入
    from src.services.cache_service import get_cache_service

    backend = get_cache_service()
    if backend is None:
        logger.debug(f"缓存后端不可用，跳过写入（no-op 降级）: {key}")
        return False
    try:
        await backend.set(key, _to_serializable(value), ttl=ttl, region=_CACHE_REGION)
        return True
    except Exception as e:
        logger.warning(f"后备缓存写入失败（忽略）: key={key}, 错误: {e}")
        return False


async def write_episode_comment_cache(
    episode_id: int, comments: list, ttl: int = COMMENTS_FETCH_CACHE_TTL
) -> bool:
    """写入弹幕缓存，供 /comment 接口快速返回。

    完整键：COMMENTS_FETCH_CACHE_PREFIX + f"comments_{episode_id}"
           = "comments_fetch_comments_{episode_id}"
    TTL：默认 300 秒（5 分钟）
    """
    key = f"{COMMENTS_FETCH_CACHE_PREFIX}comments_{episode_id}"
    return await _safe_set(key, comments, ttl)


async def write_series_fallback_cache(
    real_anime_id: int, source_order: int, info: dict, ttl: int = SERIES_FALLBACK_CACHE_TTL
) -> bool:
    """写入整季基准缓存，供连续播放时下一集请求命中并触发后备下载。

    完整键：f"fallback_episode_{baseId}"（**无前缀**，对照 comments.py:665）
           baseId = 25{real_anime_id:06d}{source_order:02d}0000
    TTL：默认 10800 秒（3 小时）

    Args:
        info: 整部剧缓存数据（real_anime_id/provider/mediaId/final_title/
              original_title/final_season/media_type/imageUrl/year/total_episodes）
    """
    base_id = encode_series_base_id(real_anime_id, source_order)
    key = f"fallback_episode_{base_id}"
    return await _safe_set(key, info, ttl)


async def write_match_season_cache(
    pure_title: str, season: int, info: dict, ttl: int = MATCH_SEASON_CACHE_TTL
) -> bool:
    """写入整季匹配缓存，供后续 /match 请求直接命中（避免重新搜索）。

    完整键：FALLBACK_SEARCH_CACHE_PREFIX + f"match_season_{pure_title}_{season}"
           = "fallback_search_match_season_{pure_title}_{season}"（对照 comments.py:713）
    TTL：默认 3600 秒（1 小时）

    why: 由后备下载写入，但被后备匹配读取（跨流程复用）。
         pure_title 需为 parse_search_keyword 解析后的纯标题，与读取端保持一致。

    Args:
        info: 整季匹配数据（provider/mediaId/real_anime_id/virtual_anime_id/
              final_title/original_title/final_season/source_order/media_type/
              imageUrl/year/timestamp）
    """
    key = f"{FALLBACK_SEARCH_CACHE_PREFIX}match_season_{pure_title}_{season}"
    return await _safe_set(key, info, ttl)
