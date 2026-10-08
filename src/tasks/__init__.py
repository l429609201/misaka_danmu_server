"""任务模块 - 拆分自原 tasks.py"""

# 纯工具由调用方直接引用 Utils，任务包不再转发。

# XML 解析由调用方直接引用 Utils，不再从任务包导出。

# 元数据反查能力统一由 MetadataService 提供，不再从任务包导出。

# 下载流程已经迁入 Workflow，任务包不再提供旧路径导出。

# 删除任务只导出业务入口，避免绕过编排层直接删除共享文件。
from .delete import (
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
from .episode_numbering import (
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
    webhook_search_and_dispatch_task,
)

# 媒体服务器任务
from .media_server import (
    scan_media_server_library,
    import_media_items,
    import_all_unimported_media_items,
)

# 后备下载任务（B类·冷启动）
from .fallback_download import (
    match_fallback_download_task,
)

__all__ = [
    # 删除任务
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
    'webhook_search_and_dispatch_task',
    # 媒体服务器任务
    'scan_media_server_library',
    'import_media_items',
    'import_all_unimported_media_items',
    # 后备下载任务（B类·冷启动）
    'match_fallback_download_task',
]

