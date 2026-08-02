"""任务模块 - 拆分自原 tasks.py"""

# 工具函数
from .utils import (
    parse_episode_ranges,
    extract_short_error_message,
    is_chinese_title,
    generate_episode_range_string,
    is_movie_by_title,
)

# XML处理
from .xml_utils import (
    parse_xml_content,
    generate_dandan_xml,
    convert_text_danmaku_to_xml,
)

# 元数据处理
from .metadata import (
    reverse_lookup_tmdb_chinese_title,
    is_tmdb_reverse_lookup_enabled,
    find_tmdb_by_external_ids,
)

# 下载辅助函数
from .download_helpers import (
    _download_episode_comments_concurrent,
    _import_episodes_iteratively,
)

# 删除任务
from .delete import (
    delete_danmaku_file,
    delete_anime_task,
    delete_source_task,
    delete_episode_task,
    delete_bulk_episodes_task,
    delete_bulk_sources_task,
)

# 刷新任务
from .refresh import (
    full_refresh_task,
    refresh_episode_task,
    refresh_bulk_episodes_task,
    incremental_refresh_task,
    fill_missing_task,
)

# 分集管理任务
from .episode_management import (
    reorder_episodes_task,
    offset_episodes_task,
)

# 核心导入任务
from .import_core import (
    generic_import_task,
    edited_import_task,
)

# 手动导入任务
from .manual_import import (
    manual_import_task,
    batch_manual_import_task,
)

# 自动导入任务
from .auto_import import (
    auto_search_and_import_task,
)

# Webhook任务
from .webhook import (
    run_webhook_tasks_directly_manual,
    webhook_search_and_dispatch_task,
)

# 媒体服务器任务
from .media_server import (
    scan_media_server_library,
    import_media_items,
    import_all_unimported_media_items,
)

__all__ = [
    # 工具函数
    'parse_episode_ranges',
    'extract_short_error_message',
    'is_chinese_title',
    'generate_episode_range_string',
    'is_movie_by_title',
    # XML处理
    'parse_xml_content',
    'generate_dandan_xml',
    'convert_text_danmaku_to_xml',
    # 元数据处理
    'reverse_lookup_tmdb_chinese_title',
    'is_tmdb_reverse_lookup_enabled',
    'find_tmdb_by_external_ids',
    # 下载辅助函数
    '_download_episode_comments_concurrent',
    '_import_episodes_iteratively',
    # 删除任务
    'delete_danmaku_file',
    'delete_anime_task',
    'delete_source_task',
    'delete_episode_task',
    'delete_bulk_episodes_task',
    'delete_bulk_sources_task',
    # 刷新任务
    'full_refresh_task',
    'refresh_episode_task',
    'refresh_bulk_episodes_task',
    'incremental_refresh_task',
    'fill_missing_task',
    # 分集管理任务
    'reorder_episodes_task',
    'offset_episodes_task',
    # 核心导入任务
    'generic_import_task',
    'edited_import_task',
    # 手动导入任务
    'manual_import_task',
    'batch_manual_import_task',
    # 自动导入任务
    'auto_search_and_import_task',
    # Webhook任务
    'run_webhook_tasks_directly_manual',
    'webhook_search_and_dispatch_task',
    # 媒体服务器任务
    'scan_media_server_library',
    'import_media_items',
    'import_all_unimported_media_items',
]

