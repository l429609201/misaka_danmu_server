"""后备流程专属工具

后备 ID 编解码保持零 IO；缓存写入辅助由 cache 子模块导出。
缓存访问统一使用 CacheService；需要数据库回退的编排通过
DatabaseService 访问仓储，不在纯算术模块中引入服务依赖。
"""

from .id_mapping import (
    encode_episode_id,
    decode_episode_id,
    encode_series_base_id,
    is_virtual_anime_id,
    is_fallback_episode_id,
)
from .cache import (
    write_episode_comment_cache,
    write_series_fallback_cache,
    write_match_season_cache,
)

__all__ = [
    # 纯算术（零IO）
    "encode_episode_id",
    "decode_episode_id",
    "encode_series_base_id",
    "is_virtual_anime_id",
    "is_fallback_episode_id",
    # 后备缓存（纯写，backend=None 时 no-op 降级）
    "write_episode_comment_cache",
    "write_series_fallback_cache",
    "write_match_season_cache",
]
