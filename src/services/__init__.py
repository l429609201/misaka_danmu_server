"""
服务层 - 各种 Manager 服务

使用方式:
    from src.services import TaskManager, TaskStatus, TaskSuccess, TaskPauseForRateLimit
    from src.services import ScraperManager, MetadataSourceManager
    from src.services import SchedulerManager, setup_logging, get_logs
"""

# 任务管理
from .task_manager import TaskManager, TaskStatus, TaskSuccess, TaskPauseForRateLimit

# 弹幕源管理
from .scraper_manager import ScraperManager

# 元数据源管理
from .metadata_manager import MetadataSourceManager

# 标题识别（需要在 WebhookManager 之前导入，因为 webhook 依赖 tasks，tasks 依赖 TitleRecognitionManager）
from .title_recognition import TitleRecognitionManager

# Webhook管理
from .webhook_manager import WebhookManager

# 媒体服务器管理
from .media_server_manager import MediaServerManager, get_media_server_manager

# 下载任务管理
from .download_task_manager import DownloadTaskManager, DownloadTask, get_download_task_manager

# 传输管理（从 utils 层重新导出，保持向后兼容）
from src.utils import TransportManager

# 调度器
from .scheduler import SchedulerManager

# 日志管理
from .log_manager import setup_logging, get_logs, subscribe_to_logs, unsubscribe_from_logs, list_log_files, read_log_file

# 搜索服务
from .search import unified_search

# 名称转换
from .name_converter import convert_to_chinese_title

# 别名服务
from .alias_service import (
    fetch_aliases, extract_aliases_from_details,
    validate_aliases_with_ai, save_aliases, fetch_and_save_aliases,
)

# 通知服务
from .notification_service import NotificationService
from .notification_manager import NotificationManager

__all__ = [
    # 任务管理
    'TaskManager',
    'TaskStatus',
    'TaskSuccess',
    'TaskPauseForRateLimit',
    # 弹幕源管理
    'ScraperManager',
    # 元数据源管理
    'MetadataSourceManager',
    # Webhook管理
    'WebhookManager',
    # 媒体服务器管理
    'MediaServerManager',
    'get_media_server_manager',
    # 标题识别
    'TitleRecognitionManager',
    # 下载任务管理
    'DownloadTaskManager',
    'DownloadTask',
    'get_download_task_manager',
    # 传输管理
    'TransportManager',
    # 调度器
    'SchedulerManager',
    # 日志管理
    'setup_logging',
    'get_logs',
    'subscribe_to_logs',
    'unsubscribe_from_logs',
    # 搜索服务
    'unified_search',
    # 名称转换
    'convert_to_chinese_title',
    # 别名服务
    'fetch_aliases',
    'extract_aliases_from_details',
    'validate_aliases_with_ai',
    'save_aliases',
    'fetch_and_save_aliases',
    # 通知服务
    'NotificationService',
    'NotificationManager',
]

