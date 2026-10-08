"""
工具函数模块

使用方式:
    from src.utils import parse_search_keyword
    from src.utils import SearchTimer, SEARCH_TYPE_WEBHOOK
    from src.utils import convert_to_chinese_title, clean_xml_string

注意: unified_search 属于搜索编排层，由调用方从 src.workflows.search.engine 显式导入。
"""

# 通用工具（已重组到 misc 子目录）
from .misc.common import sample_comments_evenly, clean_xml_string, handle_danmaku_likes, strip_danmaku_likes
from .misc.common import restyle_danmaku_likes

# 文件名解析 (统一模块，已移至 parsing 子目录)
from .parsing.filename_parser import (
    ParseResult,
    parse_filename,
    parse_search_keyword,
    extract_season_episode,
    extract_season_from_title,
    clean_title,
    clean_movie_title,
    normalize_title,
    normalize_title_key,
    extract_forced_metadata,
    is_movie_by_title,
    is_chinese_title,
    parse_episode_ranges,
    format_episode_ranges,
    format_parse_result_log,
    METADATA_PATTERN,
)

# 搜索计时器（已移至 diagnostics 子目录）
from .diagnostics.search_timer import (
    SearchTimer,
    SubStepTiming,
    SEARCH_TYPE_WEBHOOK,
    SEARCH_TYPE_FALLBACK_SEARCH,
    SEARCH_TYPE_FALLBACK_MATCH,
    SEARCH_TYPE_CONTROL_AUTO_IMPORT,
    SEARCH_TYPE_CONTROL_SEARCH,
    SEARCH_TYPE_HOME,
)

# 任务上下文管理（零依赖模块）
from .diagnostics.task_context import (
    set_task_session_factory,
    get_task_session_factory,
)

# 季度映射算法（纯算法，无 AI/网络/数据库依赖）
from .parsing.season_mapper import (
    title_contains_season_name,
)

# AI 季度编排由调用方直接从领域模块导入，不经纯工具包转发。

# 路径模板（实际位于 utils/storage）
from .storage.path_template import (
    DanmakuPathTemplate,
    create_danmaku_context,
    generate_danmaku_path,
)

# 纯图片处理位于 utils/misc/image_processing；下载与存储协调由 workflows 负责。
# 工具包不反向导出编排函数，避免跨层依赖。

# 播放历史和运行管理器由业务调用方从所属模块显式导入。

# HTTP Transport 管理（实际位于 utils/runtime）
from .runtime.transport_manager import TransportManager

# 别名语言识别（实际位于 utils/parsing）
from .parsing.alias_language import detect_language, classify_aliases

__all__ = [
    # 文件名解析
    'ParseResult',
    'parse_filename',
    'parse_search_keyword',
    'format_parse_result_log',
    'extract_season_episode',
    'extract_season_from_title',
    'clean_title',
    'clean_movie_title',
    'normalize_title',
    'normalize_title_key',
    'extract_forced_metadata',
    'is_movie_by_title',
    'is_chinese_title',
    'parse_episode_ranges',
    'format_episode_ranges',
    'METADATA_PATTERN',
    # 通用工具
    'sample_comments_evenly',
    'clean_xml_string',
    'handle_danmaku_likes',
    'strip_danmaku_likes',
    'restyle_danmaku_likes',
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
    'title_contains_season_name',
    # 路径模板
    'DanmakuPathTemplate',
    'create_danmaku_context',
    'generate_danmaku_path',
    # HTTP Transport 管理
    'TransportManager',
    # 别名语言识别
    'detect_language',
    'classify_aliases',
]

