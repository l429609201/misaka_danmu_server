"""任务处理器注册入口。

组合根调用本模块，把具体任务实现注入 TaskManager；API 仅按名称构建任务工厂，
从而避免 API→Tasks 和 Services→Tasks 的反向依赖。
"""

from src.services.task_manager import TaskManager
from src.tasks.auto_import import auto_search_and_import_task
from src.tasks.fallback_download import match_fallback_download_task
from src.tasks.import_core import edited_import_task, generic_import_task
from src.tasks.manual_import import manual_import_task
from src.tasks.media_server import (
    scan_media_server_library, import_media_items, import_all_unimported_media_items,
)
from src.tasks.refresh import full_refresh_task, incremental_refresh_task, refresh_episode_task
from src.tasks.webhook import webhook_search_and_dispatch_task
from src.tasks.episode_numbering import reorder_episodes_task, offset_episodes_task
from src.tasks.delete import delete_anime_task, delete_source_task, delete_episode_task
from src.tasks.local_danmaku_import import import_local_danmaku_task


def register_import_task_handlers(task_manager: TaskManager) -> None:
    """注册导入、刷新与编号处理器，供首次派发和重启恢复共用。"""
    # 本地导入也使用注册入口，Workflow 不反向依赖 Tasks。
    task_manager.register_task_handler("local_danmaku_import", import_local_danmaku_task)
    # 具体任务依赖集中在组合根，服务层只依赖注册接口。
    task_manager.register_task_handler("auto_import", auto_search_and_import_task)
    task_manager.register_task_handler("generic_import", generic_import_task)
    task_manager.register_task_handler("edited_import", edited_import_task)
    task_manager.register_task_handler("manual_import", manual_import_task)
    task_manager.register_task_handler("webhook_search", webhook_search_and_dispatch_task)
    task_manager.register_task_handler("full_refresh", full_refresh_task)
    task_manager.register_task_handler("incremental_refresh", incremental_refresh_task)
    task_manager.register_task_handler("refresh_episode", refresh_episode_task)
    task_manager.register_task_handler("media_scan", scan_media_server_library)
    task_manager.register_task_handler("import_media_items", import_media_items)
    task_manager.register_task_handler("import_all_unimported", import_all_unimported_media_items)
    task_manager.register_task_handler("match_fallback_download", match_fallback_download_task)
    # 新入口必须同步注册，避免已有持久化任务类型无法恢复。
    task_manager.register_task_handler("reorder_episodes", reorder_episodes_task)
    task_manager.register_task_handler("offset_episodes", offset_episodes_task)
    task_manager.register_task_handler("delete_anime", delete_anime_task)
    task_manager.register_task_handler("delete_source", delete_source_task)
    task_manager.register_task_handler("delete_episode", delete_episode_task)
