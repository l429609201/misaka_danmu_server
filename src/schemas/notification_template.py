"""通知模板标识与变量合同，供存储、预览和实际通知共享。"""

from typing import Any, Dict


class TemplateID:
    """与业务解析器无关的持久化模板标识。"""

    DANMAKU_IMPORT = "danmaku_import"
    DANMAKU_REFRESH = "danmaku_refresh"
    FALLBACK_PROCESSING = "fallback_processing"
    MEDIA_SCAN = "media_scan"
    SYSTEM_NOTICE = "system_notice"
    ALL = (DANMAKU_IMPORT, DANMAKU_REFRESH, FALLBACK_PROCESSING, MEDIA_SCAN, SYSTEM_NOTICE)


def empty_template_variables() -> Dict[str, Any]:
    """已声明变量缺失时留空，避免伪造统计或向用户显示 None。"""
    return dict.fromkeys((
        "status_icon", "status_name", "action_name", "task_id", "anime_title",
        "season", "episode", "episode_range", "episode_count", "media_type",
        "year", "media_id", "tmdb_id", "provider", "source", "trigger_name",
        "search_term", "search_type", "comment_count", "added_count",
        "success_count", "failed_count", "message", "duration", "error",
        "finished_at", "webhook_source", "image_url",
    ), "")
