"""
任务相关通知消息类型

从 MessagesMixin._format_event_message 迁移所有任务相关消息格式化逻辑，
每个消息类型独立维护模板，支持 Markdown 和纯文本双输出。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import (
    NotificationMessage, MessageCategory, MessageSeverity, AggregationPolicy,
)

_esc = NotificationMessage._escape_markdown


# ═══════════════════════════════════════════
# 导入类消息
# ═══════════════════════════════════════════

@dataclass
class ImportMessage(NotificationMessage):
    """导入成功/失败 (import_success / import_failed)"""

    def __post_init__(self):
        is_success = self.message_type == "import_success"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = self.message_type

    def to_markdown(self) -> tuple:
        return _build_import_markdown(self.payload, self.message_type)


@dataclass
class AutoImportMessage(NotificationMessage):
    """自动导入成功/失败 (auto_import_success / auto_import_failed)"""

    def __post_init__(self):
        is_success = self.message_type == "auto_import_success"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = self.message_type

    def to_markdown(self) -> tuple:
        return _build_import_markdown(self.payload, self.message_type)


@dataclass
class WebhookImportMessage(NotificationMessage):
    """Webhook 导入成功/失败"""

    def __post_init__(self):
        is_success = self.message_type == "webhook_import_success"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = self.message_type

    def to_markdown(self) -> tuple:
        return _build_import_markdown(self.payload, self.message_type)


# ═══════════════════════════════════════════
# 刷新类消息
# ═══════════════════════════════════════════

@dataclass
class RefreshMessage(NotificationMessage):
    """刷新成功/失败 (refresh_success / refresh_failed)"""

    def __post_init__(self):
        is_success = self.message_type == "refresh_success"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = self.message_type

    def to_markdown(self) -> tuple:
        return _build_refresh_markdown(self.payload, self.message_type)


@dataclass
class IncrementalRefreshMessage(NotificationMessage):
    """追更刷新成功/失败"""

    def __post_init__(self):
        is_success = self.message_type == "incremental_refresh_success"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = self.message_type
        # 追更失败启用聚合
        if not is_success:
            self.aggregation_policy = AggregationPolicy.TIME_WINDOW
            anime = self.payload.get("anime_title", "")
            self.aggregation_key = f"incr_refresh_failed:{anime}"

    def to_markdown(self) -> tuple:
        return _build_refresh_markdown(self.payload, self.message_type)


# ═══════════════════════════════════════════
# 定时任务消息
# ═══════════════════════════════════════════

@dataclass
class ScheduledTaskMessage(NotificationMessage):
    """定时任务完成/失败"""

    def __post_init__(self):
        is_success = self.message_type == "scheduled_task_complete"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = self.message_type

    def to_markdown(self) -> tuple:
        d = self.payload
        is_success = self.message_type == "scheduled_task_complete"
        icon = "✅" if is_success else "❌"
        label = "定时任务完成" if is_success else "定时任务失败"
        task_title = _esc(d.get("task_title", ""))
        task_id = d.get("task_id", "")
        message = d.get("message", "")
        finished_at = d.get("finished_at", "")
        msg_short = _esc((message[:300] + "…") if len(message) > 300 else message)

        if msg_short:
            msg_lines = [l for l in msg_short.splitlines() if l.strip()]
            if len(msg_lines) <= 1:
                detail_lines = [f"  └─ 📋 {msg_short}"]
            else:
                detail_lines = [f"  ├─ 📋 {l}" for l in msg_lines[:-1]]
                detail_lines.append(f"  └─ 📋 {msg_lines[-1]}")
        else:
            detail_lines = []

        lines = [
            "⚙️ *执行结果*",
            f"• 任务: {task_title}" if task_title else "",
            f"• 状态: {icon} {'已完成' if is_success else '执行失败'}",
            *detail_lines,
            f"• 时间: {finished_at}" if finished_at else "",
            f"• TaskID: `{task_id[:8]}…`" if task_id else "",
        ]
        return (f"{icon} {label}", "\n".join(l for l in lines if l))


# ═══════════════════════════════════════════
# 后备任务消息
# ═══════════════════════════════════════════

@dataclass
class FallbackDownloadMessage(NotificationMessage):
    """后备弹幕下载完成/失败（旧，保留兼容）"""

    def __post_init__(self):
        is_success = self.message_type == "download_fallback_success"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = "download_fallback_complete"

    def to_markdown(self) -> tuple:
        return _build_fallback_markdown(self.payload, self.message_type)


@dataclass
class FallbackSearchMessage(NotificationMessage):
    """后备搜索完成/失败"""

    def __post_init__(self):
        is_success = self.message_type == "fallback_search_success"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = "fallback_search_complete"

    def to_markdown(self) -> tuple:
        return _build_fallback_markdown(self.payload, self.message_type)


@dataclass
class PredownloadMessage(NotificationMessage):
    """预下载完成/失败"""

    def __post_init__(self):
        is_success = self.message_type == "predownload_success"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = "predownload_complete"

    def to_markdown(self) -> tuple:
        d = self.payload
        is_success = "success" in self.message_type
        icon = "✅" if is_success else "❌"
        task_title = _esc(d.get("task_title", ""))
        message = d.get("message", "")
        finished_at = d.get("finished_at", "")
        msg_short = _esc((message[:300] + "…") if len(message) > 300 else message)
        lines = [
            "📺 *媒体信息*",
            f"• 任务: {task_title}" if task_title else "",
            "",
            "⚙️ *执行结果*",
            f"• 状态: {icon} {'处理完成' if is_success else '处理失败'}",
            f"  └─ 📋 {msg_short}" if msg_short else "",
            f"• 时间: {finished_at}" if finished_at else "",
        ]
        return (f"{icon} 后备任务{'完成' if is_success else '失败'}", "\n".join(l for l in lines if l))


@dataclass
class MatchFallbackMessage(NotificationMessage):
    """匹配后备完成/失败"""

    def __post_init__(self):
        is_success = self.message_type == "match_fallback_success"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.SUCCESS if is_success else MessageSeverity.ERROR
        self.subscription_key = "match_fallback_complete"

    def to_markdown(self) -> tuple:
        d = self.payload
        is_success = "success" in self.message_type
        icon = "✅" if is_success else "❌"
        task_title = _esc(d.get("task_title", ""))
        message = d.get("message", "")
        finished_at = d.get("finished_at", "")
        msg_short = _esc((message[:300] + "…") if len(message) > 300 else message)
        task_params = d.get("task_parameters", {})
        provider = _esc(task_params.get("provider", ""))
        final_title = _esc(task_params.get("final_title", ""))
        final_season = task_params.get("final_season")
        episode_number = task_params.get("episode_number")
        is_movie = task_params.get("is_movie", False)

        lines = [
            "📺 *媒体信息*",
            f"• 任务: {task_title}" if task_title else "",
        ]
        if provider:
            lines.append(f"• 弹幕源: {provider}")
        if final_title:
            lines.append(f"• 番剧: {final_title}")
        if final_season is not None and episode_number is not None:
            if is_movie:
                lines.append("• 类型: 电影")
            else:
                lines.append(f"• 季集: S{final_season:02d}E{episode_number:02d}")
        lines.extend([
            "",
            "⚙️ *执行结果*",
            f"• 状态: {icon} {'处理完成' if is_success else '处理失败'}",
            f"  └─ 📋 {msg_short}" if msg_short else "",
            f"• 时间: {finished_at}" if finished_at else "",
        ])
        return (f"{icon} 后备任务{'完成' if is_success else '失败'}", "\n".join(l for l in lines if l))


# ═══════════════════════════════════════════
# 公共格式化辅助函数
# ═══════════════════════════════════════════

def _build_import_markdown(d: dict, event_type: str) -> tuple:
    """导入/自动导入/Webhook导入 通用 Markdown 格式"""
    _LABELS = {
        "import_success": ("导入成功", True),
        "import_failed": ("导入失败", False),
        "auto_import_success": ("自动导入成功", True),
        "auto_import_failed": ("自动导入失败", False),
        "webhook_import_success": ("Webhook 导入成功", True),
        "webhook_import_failed": ("Webhook 导入失败", False),
    }
    label, is_success = _LABELS.get(event_type, (event_type, True))
    icon = "✅" if is_success else "❌"
    status_str = "处理完成" if is_success else "处理失败"

    anime_title = _esc(d.get("anime_title", ""))
    season = d.get("season")
    episode = d.get("episode")
    source = _esc(d.get("source", ""))
    tmdb_id = d.get("tmdb_id", "")
    media_type = _esc(d.get("media_type", ""))
    task_id = d.get("task_id", "")
    message = d.get("message", "")
    finished_at = d.get("finished_at", "")
    msg_short = _esc((message[:300] + "…") if len(message) > 300 else message)

    s_str = f"S{int(season):02d}" if season is not None else ""
    e_str = f"E{int(episode):02d}" if episode is not None else ""
    tmdb_str = f"TMDB:{tmdb_id}" if tmdb_id else ""

    lines = [
        "📺 *媒体信息*",
        f"• 名称: {anime_title}" if anime_title else "",
        f"• 季集: {s_str}{e_str}" if s_str or e_str else "",
        f"• 类型: {media_type}" if media_type else "",
        f"• 来源: {source}" if source else "",
        f"• ID: {tmdb_str}" if tmdb_str else "",
        "",
        "⚙️ *任务执行信息*",
        f"• TaskID: `{task_id}`" if task_id else "",
        f"  └─ 状态: {icon} {status_str}",
        f"  └─ 📋 {msg_short}" if msg_short else "",
        f"• 时间: {finished_at}" if finished_at else "",
    ]
    return (f"{icon} {label}", "\n".join(l for l in lines if l))


def _build_refresh_markdown(d: dict, event_type: str) -> tuple:
    """刷新类消息通用 Markdown 格式"""
    _LABELS = {
        "refresh_success": ("刷新成功", True),
        "refresh_failed": ("刷新失败", False),
        "incremental_refresh_success": ("追更刷新成功", True),
        "incremental_refresh_failed": ("追更刷新失败", False),
    }
    label, is_success = _LABELS.get(event_type, (event_type, True))
    icon = "✅" if is_success else "❌"
    status_str = "处理完成" if is_success else "处理失败"

    anime_title = _esc(d.get("anime_title", ""))
    season = d.get("season")
    episode = d.get("episode")
    message = d.get("message", "")
    finished_at = d.get("finished_at", "")
    msg_short = _esc((message[:300] + "…") if len(message) > 300 else message)

    s_str = f"S{int(season):02d}" if season is not None else ""
    e_str = f"E{int(episode):02d}" if episode is not None else ""

    lines = [
        "📺 *媒体信息*",
        f"• 名称: {anime_title}" if anime_title else "",
        f"• 季集: {s_str}{e_str}" if s_str else "",
        f"• 操作: 刷新弹幕",
        f"• 状态: {icon} {status_str}",
        f"• 信息: {msg_short}" if msg_short else "",
        f"• 时间: {finished_at}" if finished_at else "",
    ]
    return (f"{icon} {label}", "\n".join(l for l in lines if l))


def _build_fallback_markdown(d: dict, event_type: str) -> tuple:
    """后备弹幕下载/搜索 通用 Markdown 格式"""
    is_success = "success" in event_type
    icon = "✅" if is_success else "❌"
    task_title = _esc(d.get("task_title", ""))
    token_name = _esc(d.get("token_name", ""))
    task_id = d.get("task_id", "")
    message = d.get("message", "")
    finished_at = d.get("finished_at", "")
    msg_short = _esc((message[:300] + "…") if len(message) > 300 else message)

    lines = [
        "📺 *媒体信息*",
        f"• 任务: {task_title}" if task_title else "",
        f"• 调用者: {token_name}" if token_name else "",
        "",
        "⚙️ *执行结果*",
        f"• TaskID: `{task_id}`" if task_id else "",
        f"  └─ 状态: {icon} {'已完成' if is_success else '失败'}",
        f"  └─ 📋 {msg_short}" if msg_short else "",
        f"• 时间: {finished_at}" if finished_at else "",
    ]
    return (f"{icon} 后备任务{'完成' if is_success else '失败'}", "\n".join(l for l in lines if l))


# ═══════════════════════════════════════════
# 聚合汇总消息
# ═══════════════════════════════════════════

@dataclass
class AggregatedSummaryMessage(NotificationMessage):
    """聚合汇总消息 — 由 Aggregator 生成"""

    def __post_init__(self):
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.WARNING
        self.subscription_key = self.payload.get("original_subscription_key", "")

    def to_markdown(self) -> tuple:
        count = self.payload.get("count", 0)
        items = self.payload.get("items", [])
        time_range = self.payload.get("time_range", "")
        # 按作品名分组统计
        anime_counts: Dict[str, int] = {}
        for item in items:
            name = item.get("anime_title", "未知")
            anime_counts[name] = anime_counts.get(name, 0) + 1

        lines = [
            f"⚠️ *批量通知汇总* ({count} 条)",
            f"⏰ 时间范围: {time_range}" if time_range else "",
            "",
        ]
        for name, cnt in anime_counts.items():
            lines.append(f"  • {_esc(name)}: {cnt} 条")

        # 附带最后一条错误摘要
        if items:
            last_msg = items[-1].get("message", "")
            if last_msg:
                short = (last_msg[:200] + "…") if len(last_msg) > 200 else last_msg
                lines.extend(["", f"📋 最近错误: {_esc(short)}"])

        return ("⚠️ 批量通知汇总", "\n".join(l for l in lines if l))
