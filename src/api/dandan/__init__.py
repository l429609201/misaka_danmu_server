"""
弹弹Play兼容API模块

使用方式:
    from src.api.dandan import dandan_router
    from src.api.dandan import apply_random_color, apply_blacklist_filter
    from src.api.dandan.models import DandanResponseBase, DandanMatchResponse
    from src.api.dandan.dependencies import get_config_manager, get_task_manager
    from src.api.dandan.constants import DANDAN_TYPE_MAPPING
    from src.api.dandan.route_handler import DandanApiRoute, get_token_from_path
"""

from fastapi import APIRouter

# 路由处理器
from .route_handler import (
    DandanApiRoute,
    get_token_from_path,
)

# Pydantic 模型
from .models import (
    DandanResponseBase,
    DandanEpisodeInfo,
    DandanAnimeInfo,
    DandanSearchEpisodesResponse,
    DandanSearchAnimeItem,
    DandanSearchAnimeResponse,
    BangumiTitle,
    BangumiEpisodeSeason,
    BangumiEpisode,
    BangumiIntro,
    BangumiTag,
    BangumiOnlineDatabase,
    BangumiTrailer,
    BangumiDetails,
    BangumiDetailsResponse,
    DandanMatchInfo,
    DandanMatchResponse,
    DandanBatchMatchRequestItem,
    DandanBatchMatchRequest,
)

# 依赖项函数
from .dependencies import (
    get_config_manager,
    get_task_manager,
    get_metadata_manager,
    get_rate_limiter,
    get_scraper_manager,
)

# 常量
from .constants import (
    DANDAN_TYPE_MAPPING,
    DANDAN_TYPE_DESC_MAPPING,
    FALLBACK_SEARCH_BANGUMI_ID,
    SAMPLED_CACHE_TTL,
    EPISODE_MAPPING_CACHE_PREFIX,
    FALLBACK_SEARCH_CACHE_PREFIX,
    TOKEN_SEARCH_TASKS_PREFIX,
    USER_LAST_BANGUMI_CHOICE_PREFIX,
    COMMENTS_FETCH_CACHE_PREFIX,
    SAMPLED_COMMENTS_CACHE_PREFIX,
    FALLBACK_SEARCH_CACHE_TTL,
    TOKEN_SEARCH_TASKS_TTL,
    USER_LAST_BANGUMI_CHOICE_TTL,
    COMMENTS_FETCH_CACHE_TTL,
    SAMPLED_COMMENTS_CACHE_TTL_DB,
)

# 弹幕颜色处理
from .danmaku_color import (
    DEFAULT_RANDOM_COLOR_MODE,
    DEFAULT_RANDOM_COLOR_PALETTE,
    apply_random_color,
    parse_palette,
)

# 弹幕过滤
from .danmaku_filter import apply_blacklist_filter

# 弹幕解析
from .danmaku_parser import parse_dandan_xml_to_comments

# 后备搜索
from .fallback_search import (
    handle_fallback_search,
    execute_fallback_search_task,
    search_implementation,
)

# 匹配功能
from .match import (
    parse_filename_for_match,
    get_match_for_item,
)

# 弹幕评论功能
from .comments import (
    comments_router,
    process_comments_for_dandanplay,
    get_external_comments_from_url,
    get_comments_for_dandan,
)

# 番剧详情功能
from .bangumi import (
    bangumi_router,
    generate_episode_id,
    get_bangumi_details,
)

# 搜索功能
from .search import (
    search_router,
    search_episodes_for_dandan,
    search_anime_for_dandan,
)

# 匹配功能路由
from .match import (
    match_router,
    parse_filename_for_match,
    get_match_for_item,
    match_single_file,
    match_batch_files,
)

# 预下载功能
from .predownload import (
    wait_for_refresh_task,
    try_predownload_next_episode,
)

__all__ = [
    # 路由处理器
    'DandanApiRoute',
    'get_token_from_path',
    # Pydantic 模型
    'DandanResponseBase',
    'DandanEpisodeInfo',
    'DandanAnimeInfo',
    'DandanSearchEpisodesResponse',
    'DandanSearchAnimeItem',
    'DandanSearchAnimeResponse',
    'BangumiTitle',
    'BangumiEpisodeSeason',
    'BangumiEpisode',
    'BangumiIntro',
    'BangumiTag',
    'BangumiOnlineDatabase',
    'BangumiTrailer',
    'BangumiDetails',
    'BangumiDetailsResponse',
    'DandanMatchInfo',
    'DandanMatchResponse',
    'DandanBatchMatchRequestItem',
    'DandanBatchMatchRequest',
    # 依赖项函数
    'get_config_manager',
    'get_task_manager',
    'get_metadata_manager',
    'get_rate_limiter',
    'get_scraper_manager',
    # 常量
    'DANDAN_TYPE_MAPPING',
    'DANDAN_TYPE_DESC_MAPPING',
    'FALLBACK_SEARCH_BANGUMI_ID',
    'SAMPLED_CACHE_TTL',
    'EPISODE_MAPPING_CACHE_PREFIX',
    'FALLBACK_SEARCH_CACHE_PREFIX',
    'TOKEN_SEARCH_TASKS_PREFIX',
    'USER_LAST_BANGUMI_CHOICE_PREFIX',
    'COMMENTS_FETCH_CACHE_PREFIX',
    'SAMPLED_COMMENTS_CACHE_PREFIX',
    'FALLBACK_SEARCH_CACHE_TTL',
    'TOKEN_SEARCH_TASKS_TTL',
    'USER_LAST_BANGUMI_CHOICE_TTL',
    'COMMENTS_FETCH_CACHE_TTL',
    'SAMPLED_COMMENTS_CACHE_TTL_DB',
    # 弹幕颜色
    'DEFAULT_RANDOM_COLOR_MODE',
    'DEFAULT_RANDOM_COLOR_PALETTE',
    'apply_random_color',
    'parse_palette',
    # 弹幕过滤
    'apply_blacklist_filter',
    # 弹幕解析
    'parse_danmaku_xml',
    'DanmakuParser',
    # 后备搜索
    'handle_fallback_search',
    'execute_fallback_search_task',
    'search_implementation',
    # 匹配功能
    'match_router',
    'parse_filename_for_match',
    'get_match_for_item',
    'match_single_file',
    'match_batch_files',
    # 弹幕评论功能
    'comments_router',
    'process_comments_for_dandanplay',
    'get_external_comments_from_url',
    'get_comments_for_dandan',
    # 番剧详情功能
    'bangumi_router',
    'generate_episode_id',
    'get_bangumi_details',
    # 搜索功能
    'search_router',
    'search_episodes_for_dandan',
    'search_anime_for_dandan',
    # 预下载功能
    'wait_for_refresh_task',
    'try_predownload_next_episode',
    # 主路由
    'dandan_router',
]


# ==================== 主路由创建和挂载 ====================

# 这是将包含在 main.py 中的主路由。
# 使用自定义的 Route 类来应用特殊的异常处理。
dandan_router = APIRouter(route_class=DandanApiRoute)

# --- 路由挂载 ---
# 将各子模块的路由挂载到主路由上，以支持两种URL结构。

# 挂载以支持兼容路径: /{token}/api/v2/...
dandan_router.include_router(comments_router, prefix="/{token}/api/v2")
dandan_router.include_router(bangumi_router, prefix="/{token}/api/v2")
dandan_router.include_router(search_router, prefix="/{token}/api/v2")
dandan_router.include_router(match_router, prefix="/{token}/api/v2")

# 挂载以支持直接路径: /{token}/...
dandan_router.include_router(comments_router, prefix="/{token}")
dandan_router.include_router(bangumi_router, prefix="/{token}")
dandan_router.include_router(search_router, prefix="/{token}")
dandan_router.include_router(match_router, prefix="/{token}")

