"""
工具函数模块

使用方式:
    from src.utils import parse_search_keyword
    from src.utils import SearchTimer, SEARCH_TYPE_WEBHOOK
    from src.utils import convert_to_chinese_title, clean_xml_string

注意: unified_search 已移至 src.services 层:
    from src.services import unified_search
"""

# 通用工具
from .common import sample_comments_evenly, clean_xml_string

# 文件名解析 (统一模块)
from .filename_parser import (
    ParseResult,
    parse_filename,
    parse_search_keyword,
    extract_season_episode,
    extract_season_from_title,
    clean_title,
    clean_movie_title,
    normalize_title,
    is_movie_by_title,
    is_chinese_title,
    parse_episode_ranges,
    format_episode_ranges,
    METADATA_PATTERN,
)

# 搜索计时器
from .search_timer import (
    SearchTimer,
    SubStepTiming,
    SEARCH_TYPE_WEBHOOK,
    SEARCH_TYPE_FALLBACK_SEARCH,
    SEARCH_TYPE_FALLBACK_MATCH,
    SEARCH_TYPE_CONTROL_AUTO_IMPORT,
    SEARCH_TYPE_CONTROL_SEARCH,
    SEARCH_TYPE_HOME,
)

# 季度映射
from .season_mapper import (
    ai_type_and_season_mapping_and_correction,
    title_contains_season_name,
)

# 路径模板
from .path_template import (
    DanmakuPathTemplate,
    create_danmaku_context,
    generate_danmaku_path,
)

# 图片工具
from .image_utils import download_image

# 播放历史
from .play_history import record_play_history

# 内部轮询
from .internal_polling import InternalPollingManager

# 代理中间件
from .proxy_middleware import init_proxy_middleware

# HTTP Transport 管理
from .transport_manager import TransportManager

__all__ = [
    # 文件名解析
    'ParseResult',
    'parse_filename',
    'parse_search_keyword',
    'extract_season_episode',
    'extract_season_from_title',
    'clean_title',
    'clean_movie_title',
    'normalize_title',
    'is_movie_by_title',
    'is_chinese_title',
    'parse_episode_ranges',
    'format_episode_ranges',
    'METADATA_PATTERN',
    # 通用工具
    'sample_comments_evenly',
    'clean_xml_string',
    # 搜索计时器
    'SearchTimer',
    'SubStepTiming',
    'SEARCH_TYPE_WEBHOOK',
    'SEARCH_TYPE_FALLBACK_SEARCH',
    'SEARCH_TYPE_FALLBACK_MATCH',
    'SEARCH_TYPE_CONTROL_AUTO_IMPORT',
    'SEARCH_TYPE_CONTROL_SEARCH',
    'SEARCH_TYPE_HOME',
    # 季度映射
    'ai_type_and_season_mapping_and_correction',
    'title_contains_season_name',
    # 路径模板
    'DanmakuPathTemplate',
    'create_danmaku_context',
    'generate_danmaku_path',
    # 图片工具
    'download_image',
    # 播放历史
    'record_play_history',
    # 内部轮询
    'InternalPollingManager',
    # 代理中间件
    'init_proxy_middleware',
    # HTTP Transport 管理
    'TransportManager',
]

