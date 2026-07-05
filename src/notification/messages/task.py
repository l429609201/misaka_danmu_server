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

    def to_text(self) -> tuple:
        return _build_import_text(self.payload, self.message_type)


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

    def to_text(self) -> tuple:
        return _build_import_text(self.payload, self.message_type)


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

    def to_text(self) -> tuple:
        return _build_import_text(self.payload, self.message_type)


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

    def to_text(self) -> tuple:
        return _build_refresh_text(self.payload, self.message_type)


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

    def to_text(self) -> tuple:
        return _build_refresh_text(self.payload, self.message_type)


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
        title = f"{icon} *定时任务{'完成' if is_success else '失败'}*"
        task_title = _esc(d.get("task_title", ""))
        message = d.get("message", "")
        finished_at = d.get("finished_at", "")
        msg_short = _esc((message[:200] + "…") if len(message) > 200 else message)

        # 引用块
        quote_lines = []
        if task_title:
            quote_lines.append(f">⚙️ 任务: {task_title}")

        # 组装
        lines = [title, ""]
        lines.extend(quote_lines)
        lines.append("")
        if msg_short:
            lines.append(f"📋 {msg_short}")
        if finished_at:
            lines.append(f"⏰ {_esc(finished_at)}")

        return (title.replace("*", ""), "\n".join(l for l in lines if l is not None))

    def to_text(self) -> tuple:
        d = self.payload
        is_success = self.message_type == "scheduled_task_complete"
        icon = "✅" if is_success else "❌"
        title = f"{icon} 定时任务{'完成' if is_success else '失败'}"
        task_title = d.get("task_title", "")
        message = d.get("message", "")
        finished_at = d.get("finished_at", "")
        msg_short = (message[:200] + "…") if len(message) > 200 else message

        lines = [title, ""]
        if task_title:
            lines.append(f"⚙️ 任务: {task_title}")
        if msg_short:
            lines.append("")
            lines.append(f"📋 {msg_short}")
        if finished_at:
            lines.append(f"⏰ {finished_at}")

        return (title, "\n".join(l for l in lines if l is not None))


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

    def to_text(self) -> tuple:
        return _build_fallback_text(self.payload, self.message_type)


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

    def to_text(self) -> tuple:
        return _build_fallback_text(self.payload, self.message_type)

    async def build_image_bytes(self, proxy=None, ssl_verify: bool = True):
        """聚合后备搜索结果海报为九宫格 PNG。

        海报URL由 fallback_search 在任务完成后写入 task_parameters['poster_urls']。
        仅成功事件、且有海报URL时才聚合；任何异常都吞掉返回 None，确保通知正常发出。
        开关(fallbackSearchPosterCollage)与代理由调用方(dispatch)判定后决定是否调用。
        """
        if self.message_type != "fallback_search_success":
            return None
        # poster_urls 优先取 payload 顶层，其次取 task_parameters（task_manager 透传位置）
        poster_urls = self.payload.get("poster_urls")
        if not poster_urls:
            task_params = self.payload.get("task_parameters") or {}
            poster_urls = task_params.get("poster_urls")
        if not poster_urls:
            return None
        try:
            from src.utils.poster_collage import build_poster_collage
            items = [
                {"imageUrl": url, "index": i + 1}
                for i, url in enumerate(poster_urls) if url
            ]
            if not items:
                return None
            return await build_poster_collage(items, proxy=proxy, ssl_verify=ssl_verify)
        except Exception:
            # 聚合失败静默降级为纯文字通知
            return None


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
        title = f"{icon} *预下载{'完成' if is_success else '失败'}*"
        message = d.get("message", "")
        finished_at = d.get("finished_at", "")
        msg_short = _esc((message[:200] + "…") if len(message) > 200 else message)

        # 结构化字段（与匹配后备通知统一）：优先 payload 顶层（task_manager 已提取），
        # 缺失时回退 task_parameters。
        task_params = d.get("task_parameters", {}) or {}
        anime_title = _esc(d.get("anime_title") or task_params.get("anime_title", ""))
        season = d.get("season") if d.get("season") is not None else task_params.get("season")
        episode = d.get("episode") if d.get("episode") is not None else task_params.get("episode")
        provider = _esc(d.get("provider") or task_params.get("provider", ""))
        is_movie = task_params.get("is_movie", False)

        # 引用块（结构化信息）
        quote_lines = []
        if anime_title:
            quote_lines.append(f">📺 *{anime_title}*")
        if not is_movie and season is not None and episode is not None:
            quote_lines.append(f">📍 S{int(season):02d}E{int(episode):02d}")
        elif is_movie:
            quote_lines.append(">📍 电影")
        if provider:
            quote_lines.append(f">🎯 弹幕源: {provider}")

        # 组装
        lines = [title, ""]
        lines.extend(quote_lines)
        lines.append("")
        if msg_short:
            lines.append(f"📋 {msg_short}")
        if finished_at:
            lines.append(f"⏰ {_esc(finished_at)}")

        return (title.replace("*", ""), "\n".join(l for l in lines if l is not None))

    def to_text(self) -> tuple:
        d = self.payload
        is_success = "success" in self.message_type
        icon = "✅" if is_success else "❌"
        title = f"{icon} 预下载{'完成' if is_success else '失败'}"
        message = d.get("message", "")
        finished_at = d.get("finished_at", "")
        msg_short = (message[:200] + "…") if len(message) > 200 else message

        task_params = d.get("task_parameters", {}) or {}
        anime_title = d.get("anime_title") or task_params.get("anime_title", "")
        season = d.get("season") if d.get("season") is not None else task_params.get("season")
        episode = d.get("episode") if d.get("episode") is not None else task_params.get("episode")
        provider = d.get("provider") or task_params.get("provider", "")
        is_movie = task_params.get("is_movie", False)

        lines = [title, ""]
        if anime_title:
            lines.append(f"📺 {anime_title}")
        if not is_movie and season is not None and episode is not None:
            lines.append(f"📍 S{int(season):02d}E{int(episode):02d}")
        elif is_movie:
            lines.append("📍 电影")
        if provider:
            lines.append(f"🎯 弹幕源: {provider}")
        if msg_short:
            lines.append("")
            lines.append(f"📋 {msg_short}")
        if finished_at:
            lines.append(f"⏰ {finished_at}")

        return (title, "\n".join(l for l in lines if l is not None))


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
        title = f"{icon} *匹配后备{'完成' if is_success else '失败'}*"
        message = d.get("message", "")
        finished_at = d.get("finished_at", "")
        msg_short = _esc((message[:200] + "…") if len(message) > 200 else message)
        task_params = d.get("task_parameters", {})
        provider = _esc(task_params.get("provider", ""))
        final_title = _esc(task_params.get("final_title", ""))
        final_season = task_params.get("final_season")
        episode_number = task_params.get("episode_number")
        is_movie = task_params.get("is_movie", False)

        # 引用块
        quote_lines = []
        if final_title:
            quote_lines.append(f">📺 *{final_title}*")
        if final_season is not None and episode_number is not None:
            if is_movie:
                quote_lines.append(">📍 电影")
            else:
                quote_lines.append(f">📍 S{final_season:02d}E{episode_number:02d}")
        if provider:
            quote_lines.append(f">🎯 弹幕源: {provider}")

        # 组装
        lines = [title, ""]
        lines.extend(quote_lines)
        lines.append("")
        if msg_short:
            lines.append(f"📋 {msg_short}")
        if finished_at:
            lines.append(f"⏰ {_esc(finished_at)}")

        return (title.replace("*", ""), "\n".join(l for l in lines if l is not None))

    def to_text(self) -> tuple:
        d = self.payload
        is_success = "success" in self.message_type
        icon = "✅" if is_success else "❌"
        title = f"{icon} 匹配后备{'完成' if is_success else '失败'}"
        message = d.get("message", "")
        finished_at = d.get("finished_at", "")
        msg_short = (message[:200] + "…") if len(message) > 200 else message
        task_params = d.get("task_parameters", {})
        provider = task_params.get("provider", "")
        final_title = task_params.get("final_title", "")
        final_season = task_params.get("final_season")
        episode_number = task_params.get("episode_number")
        is_movie = task_params.get("is_movie", False)

        lines = [title, ""]
        if final_title:
            lines.append(f"📺 {final_title}")
        if final_season is not None and episode_number is not None:
            if is_movie:
                lines.append("📍 电影")
            else:
                lines.append(f"📍 S{final_season:02d}E{episode_number:02d}")
        if provider:
            lines.append(f"🎯 弹幕源: {provider}")
        if msg_short:
            lines.append("")
            lines.append(f"📋 {msg_short}")
        if finished_at:
            lines.append(f"⏰ {finished_at}")

        return (title, "\n".join(l for l in lines if l is not None))


# ═══════════════════════════════════════════
# 公共格式化辅助函数
# ═══════════════════════════════════════════

def _build_import_markdown(d: dict, event_type: str) -> tuple:
    """导入/自动导入/Webhook导入 通用 MarkdownV2 卡片式模板"""
    _LABELS = {
        "import_success": ("✅ *导入成功*", True),
        "import_failed": ("❌ *导入失败*", False),
        "auto_import_success": ("✅ *自动导入成功*", True),
        "auto_import_failed": ("❌ *自动导入失败*", False),
        "webhook_import_success": ("✅ *Webhook 导入成功*", True),
        "webhook_import_failed": ("❌ *Webhook 导入失败*", False),
    }
    title, is_success = _LABELS.get(event_type, (f"{'✅' if 'success' in event_type else '❌'} *{event_type}*", "success" in event_type))

    anime_title = _esc(d.get("anime_title", ""))
    season = d.get("season")
    episode = d.get("episode")
    source = _esc(d.get("source", ""))
    tmdb_id = d.get("tmdb_id", "")
    media_type = _esc(d.get("media_type", ""))
    year = d.get("year")
    image_url = d.get("image_url", "") or d.get("imageUrl", "")
    message = d.get("message", "")
    finished_at = d.get("finished_at", "")
    msg_short = _esc((message[:200] + "…") if len(message) > 200 else message)

    s_str = f"S{int(season):02d}" if season is not None else ""
    e_str = f"E{int(episode):02d}" if episode is not None else ""
    se_str = f"{s_str}{e_str}" if s_str or e_str else ""

    # 标题行：附带年份，如 📺 标题 (2024)
    title_with_year = f"{anime_title} ({int(year)})" if (anime_title and year) else anime_title

    # 引用块内容
    quote_lines = []
    if title_with_year:
        quote_lines.append(f">📺 *{title_with_year}*")
    if se_str or media_type:
        parts = [p for p in [se_str, media_type] if p]
        quote_lines.append(f">📍 {' \\| '.join(parts)}")
    if source:
        quote_lines.append(f">🎯 弹幕源: {source}")
    if tmdb_id:
        quote_lines.append(f">🏷️ TMDB: {_esc(str(tmdb_id))}")
    if image_url:
        quote_lines.append(f">🖼 [海报]({_esc(str(image_url))})")

    # 组装完整消息
    lines = [title, ""]
    lines.extend(quote_lines)
    lines.append("")
    if msg_short:
        lines.append(f"📋 {msg_short}")
    if finished_at:
        lines.append(f"⏰ {_esc(finished_at)}")

    return (title.replace("*", ""), "\n".join(l for l in lines if l is not None))


def _build_import_text(d: dict, event_type: str) -> tuple:
    """导入类 纯文本模板（企业微信/Server酱，无任何 markdown 符号）"""
    _LABELS = {
        "import_success": ("✅ 导入成功", True),
        "import_failed": ("❌ 导入失败", False),
        "auto_import_success": ("✅ 自动导入成功", True),
        "auto_import_failed": ("❌ 自动导入失败", False),
        "webhook_import_success": ("✅ Webhook 导入成功", True),
        "webhook_import_failed": ("❌ Webhook 导入失败", False),
    }
    title, _ = _LABELS.get(event_type, (f"{'✅' if 'success' in event_type else '❌'} {event_type}", True))

    anime_title = d.get("anime_title", "")
    season = d.get("season")
    episode = d.get("episode")
    source = d.get("source", "")
    tmdb_id = d.get("tmdb_id", "")
    media_type = d.get("media_type", "")
    year = d.get("year")
    image_url = d.get("image_url", "") or d.get("imageUrl", "")
    message = d.get("message", "")
    finished_at = d.get("finished_at", "")
    msg_short = (message[:200] + "…") if len(message) > 200 else message

    s_str = f"S{int(season):02d}" if season is not None else ""
    e_str = f"E{int(episode):02d}" if episode is not None else ""
    se_str = f"{s_str}{e_str}" if s_str or e_str else ""

    title_with_year = f"{anime_title} ({int(year)})" if (anime_title and year) else anime_title

    lines = [title, ""]
    if title_with_year:
        lines.append(f"📺 {title_with_year}")
    if se_str or media_type:
        parts = [p for p in [se_str, media_type] if p]
        lines.append(f"📍 {' | '.join(parts)}")
    if source:
        lines.append(f"🎯 弹幕源: {source}")
    if tmdb_id:
        lines.append(f"🏷️ TMDB: {tmdb_id}")
    if image_url:
        lines.append(f"🖼 海报: {image_url}")
    if msg_short:
        lines.append("")
        lines.append(f"📋 {msg_short}")
    if finished_at:
        lines.append(f"⏰ {finished_at}")

    return (title, "\n".join(l for l in lines if l is not None))


def _build_refresh_markdown(d: dict, event_type: str) -> tuple:
    """刷新类消息 MarkdownV2 卡片式模板"""
    _LABELS = {
        "refresh_success": ("✅ *刷新成功*", True),
        "refresh_failed": ("❌ *刷新失败*", False),
        "incremental_refresh_success": ("✅ *追更成功*", True),
        "incremental_refresh_failed": ("❌ *追更失败*", False),
    }
    title, is_success = _LABELS.get(event_type, (f"{'✅' if 'success' in event_type else '❌'} *刷新*", "success" in event_type))

    anime_title = _esc(d.get("anime_title", ""))
    season = d.get("season")
    episode = d.get("episode")
    year = d.get("year")
    image_url = d.get("image_url", "") or d.get("imageUrl", "")
    message = d.get("message", "")
    finished_at = d.get("finished_at", "")
    msg_short = _esc((message[:200] + "…") if len(message) > 200 else message)

    s_str = f"S{int(season):02d}" if season is not None else ""
    e_str = f"E{int(episode):02d}" if episode is not None else ""
    se_str = f"{s_str}{e_str}" if s_str else ""

    # 操作类型
    is_incremental = "incremental" in event_type
    op_str = "增量追更" if is_incremental else "手动刷新"

    title_with_year = f"{anime_title} ({int(year)})" if (anime_title and year) else anime_title

    # 引用块
    quote_lines = []
    if title_with_year:
        quote_lines.append(f">📺 *{title_with_year}*")
    if se_str:
        quote_lines.append(f">📍 {se_str}")
    quote_lines.append(f">🔄 操作: {_esc(op_str)}")
    if image_url:
        quote_lines.append(f">🖼 [海报]({_esc(str(image_url))})")

    # 组装
    lines = [title, ""]
    lines.extend(quote_lines)
    lines.append("")
    if msg_short:
        lines.append(f"📋 {msg_short}")
    if finished_at:
        lines.append(f"⏰ {_esc(finished_at)}")

    return (title.replace("*", ""), "\n".join(l for l in lines if l is not None))


def _build_refresh_text(d: dict, event_type: str) -> tuple:
    """刷新类 纯文本模板"""
    _LABELS = {
        "refresh_success": "✅ 刷新成功",
        "refresh_failed": "❌ 刷新失败",
        "incremental_refresh_success": "✅ 追更成功",
        "incremental_refresh_failed": "❌ 追更失败",
    }
    title = _LABELS.get(event_type, f"{'✅' if 'success' in event_type else '❌'} 刷新")

    anime_title = d.get("anime_title", "")
    season = d.get("season")
    episode = d.get("episode")
    year = d.get("year")
    image_url = d.get("image_url", "") or d.get("imageUrl", "")
    message = d.get("message", "")
    finished_at = d.get("finished_at", "")
    msg_short = (message[:200] + "…") if len(message) > 200 else message

    s_str = f"S{int(season):02d}" if season is not None else ""
    e_str = f"E{int(episode):02d}" if episode is not None else ""
    se_str = f"{s_str}{e_str}" if s_str else ""
    op_str = "增量追更" if "incremental" in event_type else "手动刷新"

    title_with_year = f"{anime_title} ({int(year)})" if (anime_title and year) else anime_title

    lines = [title, ""]
    if title_with_year:
        lines.append(f"📺 {title_with_year}")
    if se_str:
        lines.append(f"📍 {se_str}")
    lines.append(f"🔄 操作: {op_str}")
    if image_url:
        lines.append(f"🖼 海报: {image_url}")
    if msg_short:
        lines.append("")
        lines.append(f"📋 {msg_short}")
    if finished_at:
        lines.append(f"⏰ {finished_at}")

    return (title, "\n".join(l for l in lines if l is not None))


def _build_fallback_markdown(d: dict, event_type: str) -> tuple:
    """后备弹幕下载/搜索 MarkdownV2 卡片式模板"""
    is_success = "success" in event_type
    icon = "✅" if is_success else "❌"
    # 区分后备下载和后备搜索
    if "search" in event_type:
        title = f"{icon} *后备搜索{'完成' if is_success else '失败'}*"
    else:
        title = f"{icon} *后备下载{'完成' if is_success else '失败'}*"

    task_title = _esc(d.get("task_title", ""))
    token_name = _esc(d.get("token_name", ""))
    message = d.get("message", "")
    finished_at = d.get("finished_at", "")
    msg_short = _esc((message[:200] + "…") if len(message) > 200 else message)

    # 结构化字段（后备下载任务已携带）：优先 payload 顶层，其次 task_parameters。
    task_params = d.get("task_parameters", {}) or {}
    anime_title = _esc(d.get("anime_title") or task_params.get("anime_title", ""))
    season = d.get("season") if d.get("season") is not None else task_params.get("season")
    episode = d.get("episode") if d.get("episode") is not None else task_params.get("episode")
    provider = _esc(d.get("provider") or task_params.get("provider", ""))
    is_movie = d.get("is_movie", task_params.get("is_movie", False))

    # 引用块：有 anime_title 走结构块（作品名/季集/弹幕源）；否则回退旧的 task_title 展示。
    quote_lines = []
    if anime_title:
        quote_lines.append(f">📺 *{anime_title}*")
        if episode is not None and season is not None:
            quote_lines.append(f">📍 S{int(season):02d}E{int(episode):02d}")
        elif is_movie:
            quote_lines.append(">📍 电影")
        if provider:
            quote_lines.append(f">🎯 弹幕源: {provider}")
    elif task_title:
        quote_lines.append(f">📺 *{task_title}*")
    if token_name:
        quote_lines.append(f">👤 调用者: {token_name}")

    # 组装
    lines = [title, ""]
    lines.extend(quote_lines)
    lines.append("")
    if msg_short:
        lines.append(f"📋 {msg_short}")
    if finished_at:
        lines.append(f"⏰ {_esc(finished_at)}")

    return (title.replace("*", ""), "\n".join(l for l in lines if l is not None))


def _build_fallback_text(d: dict, event_type: str) -> tuple:
    """后备弹幕下载/搜索 纯文本模板"""
    is_success = "success" in event_type
    icon = "✅" if is_success else "❌"
    if "search" in event_type:
        title = f"{icon} 后备搜索{'完成' if is_success else '失败'}"
    else:
        title = f"{icon} 后备下载{'完成' if is_success else '失败'}"

    task_title = d.get("task_title", "")
    token_name = d.get("token_name", "")
    message = d.get("message", "")
    finished_at = d.get("finished_at", "")
    msg_short = (message[:200] + "…") if len(message) > 200 else message

    # 结构化字段（后备下载任务已携带）：优先 payload 顶层，其次 task_parameters。
    task_params = d.get("task_parameters", {}) or {}
    anime_title = d.get("anime_title") or task_params.get("anime_title", "")
    season = d.get("season") if d.get("season") is not None else task_params.get("season")
    episode = d.get("episode") if d.get("episode") is not None else task_params.get("episode")
    provider = d.get("provider") or task_params.get("provider", "")
    is_movie = d.get("is_movie", task_params.get("is_movie", False))

    lines = [title, ""]
    if anime_title:
        lines.append(f"📺 {anime_title}")
        if episode is not None and season is not None:
            lines.append(f"📍 S{int(season):02d}E{int(episode):02d}")
        elif is_movie:
            lines.append("📍 电影")
        if provider:
            lines.append(f"🎯 弹幕源: {provider}")
    elif task_title:
        lines.append(f"📺 {task_title}")
    if token_name:
        lines.append(f"👤 调用者: {token_name}")
    if msg_short:
        lines.append("")
        lines.append(f"📋 {msg_short}")
    if finished_at:
        lines.append(f"⏰ {finished_at}")

    return (title, "\n".join(l for l in lines if l is not None))


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
        title = f"⚠️ *批量通知汇总* \\({_esc(str(count))} 条\\)"
        # 按作品名分组统计
        anime_counts: Dict[str, int] = {}
        for item in items:
            name = item.get("anime_title", "未知")
            anime_counts[name] = anime_counts.get(name, 0) + 1

        quote_lines = []
        for name, cnt in anime_counts.items():
            quote_lines.append(f">📺 {_esc(name)}: {cnt} 条")

        lines = [title, ""]
        if time_range:
            lines.append(f"⏰ {_esc(time_range)}")
            lines.append("")
        lines.extend(quote_lines)

        # 附带最后一条错误摘要
        if items:
            last_msg = items[-1].get("message", "")
            if last_msg:
                short = (last_msg[:200] + "…") if len(last_msg) > 200 else last_msg
                lines.extend(["", f"📋 最近错误: {_esc(short)}"])

        return ("⚠️ 批量通知汇总", "\n".join(l for l in lines if l))

    def to_text(self) -> tuple:
        count = self.payload.get("count", 0)
        items = self.payload.get("items", [])
        time_range = self.payload.get("time_range", "")
        title = f"⚠️ 批量通知汇总 ({count} 条)"
        anime_counts: Dict[str, int] = {}
        for item in items:
            name = item.get("anime_title", "未知")
            anime_counts[name] = anime_counts.get(name, 0) + 1

        lines = [title, ""]
        if time_range:
            lines.append(f"⏰ {time_range}")
            lines.append("")
        for name, cnt in anime_counts.items():
            lines.append(f"📺 {name}: {cnt} 条")

        if items:
            last_msg = items[-1].get("message", "")
            if last_msg:
                short = (last_msg[:200] + "…") if len(last_msg) > 200 else last_msg
                lines.extend(["", f"📋 最近错误: {short}"])

        return ("⚠️ 批量通知汇总", "\n".join(l for l in lines if l))
