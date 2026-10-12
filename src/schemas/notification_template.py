"""通知模板标识与变量合同，供存储、预览和实际通知共享。"""

from typing import Any, Dict


class TemplateID:
    """与业务解析器无关的持久化模板标识。"""

    DANMAKU_IMPORT = "danmaku_import"
    DANMAKU_REFRESH = "danmaku_refresh"
    FALLBACK_PROCESSING = "fallback_processing"
    MEDIA_SCAN = "media_scan"
    SYSTEM_NOTICE = "system_notice"
    TASK_PROGRESS = "task_progress"
    WEBHOOK = "webhook"
    SCHEDULED_TASK = "scheduled_task"
    ALL = (DANMAKU_IMPORT, DANMAKU_REFRESH, WEBHOOK, FALLBACK_PROCESSING, MEDIA_SCAN, SCHEDULED_TASK, SYSTEM_NOTICE, TASK_PROGRESS)


# 订阅项与模板的展示合同只保存标识，不依赖业务匹配器，避免反向导入。
SCOPE_TEMPLATE_IDS: Dict[str, str] = {
    "import_success": TemplateID.DANMAKU_IMPORT,
    "import_failed": TemplateID.DANMAKU_IMPORT,
    "refresh_success": TemplateID.DANMAKU_REFRESH,
    "refresh_failed": TemplateID.DANMAKU_REFRESH,
    "incremental_refresh_success": TemplateID.DANMAKU_REFRESH,
    "incremental_refresh_no_change": TemplateID.DANMAKU_REFRESH,
    "incremental_refresh_failed": TemplateID.DANMAKU_REFRESH,
    "webhook": TemplateID.WEBHOOK,
    "fallback_success": TemplateID.FALLBACK_PROCESSING,
    "fallback_failed": TemplateID.FALLBACK_PROCESSING,
    "media_scan_success": TemplateID.MEDIA_SCAN,
    "media_scan_failed": TemplateID.MEDIA_SCAN,
    "scheduled_task": TemplateID.SCHEDULED_TASK,
    "system_startup": TemplateID.SYSTEM_NOTICE,
    "system_exception": TemplateID.SYSTEM_NOTICE,
    "task_progress": TemplateID.TASK_PROGRESS,
}


def template_order(template_id: str) -> int:
    """按共享模板顺序排列展示项，未知标识放在末尾。"""
    return TemplateID.ALL.index(template_id) if template_id in TemplateID.ALL else len(TemplateID.ALL)


# 缺失模板也使用同一 Jinja 默认值，避免发送路径维护另一份硬编码格式。
DEFAULT_PROGRESS_TITLE = "{{ task_title }}"
DEFAULT_PROGRESS_BODY = "[{{ progress_bar }}] {{ progress }}%\n{{ description }}"


def empty_template_variables() -> Dict[str, Any]:
    """已声明变量缺失时留空，避免伪造统计或向用户显示 None。"""
    variables = dict.fromkeys((
        "status_icon", "status_name", "action_name", "task_id", "anime_title",
        "season", "episode", "episode_range", "episode_count", "media_type",
        "year", "media_id", "tmdb_id", "provider", "source", "trigger_name",
        "search_term", "search_type", "comment_count", "added_count",
        "success_count", "failed_count", "message", "duration", "error",
        "finished_at", "webhook_source", "image_url", "task_title",
        "progress_bar", "description",
    ), "")
    variables["progress"] = 0
    return variables
