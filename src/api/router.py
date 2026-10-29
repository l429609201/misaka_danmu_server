"""应用路由组合入口；仅由主应用导入，不参与 API 包初始化。"""

from fastapi import APIRouter

from src.api.ui import (
    auth, scraper, metadata_source, media_server, anime, source, episode,
    search, import_api, task, token, config_extra, settings, scheduled_task,
    webhook, system, local_danmaku, scraper_resources, parameters,
    danmaku_storage, backup, danmaku_edit, local_episode_group, poster,
    notification_routes, anime_group, auth_mfa, calendar, cache, debug,
    health, diagnostics, data_check, recognition_check, config_history,
    trends, audit, calendar_extra, ai_explain, scan_index, subscriptions,
    perf, assistant, assistant_sessions, oauth,
)
from src.api import webhook_api

api_router = APIRouter()

# 保持原路由路径和注册顺序，聚合行为不再由包初始化隐式触发。
for endpoint, prefix, tag in (
    (oauth.router, "/ui", "OAuth"),
    (auth.router, "/ui/auth", "Auth"),
    (scraper.router, "/ui", "Scraper"),
    (scraper_resources.router, "/ui", "Scraper Resources"),
    (parameters.router, "/ui", "Parameters"),
    (metadata_source.router, "/ui", "Metadata Source"),
    (media_server.router, "/ui", "Media Server"),
    (local_danmaku.router, "/ui", "Local Danmaku"),
    (danmaku_storage.router, "/ui/danmaku-storage", "Danmaku Storage"),
    (anime.router, "/ui", "Anime"),
    (source.router, "/ui", "Source"),
    (episode.router, "/ui", "Episode"),
    (search.router, "/ui", "Search"),
    (import_api.router, "/ui", "Import"),
    (task.router, "/ui", "Task"),
    (token.router, "/ui", "Token"),
    (settings.router, "/ui", "Settings"),
    (scheduled_task.router, "/ui", "ScheduledTask"),
    (webhook.router, "/ui", "Webhook"),
    (system.router, "/ui", "System"),
    (auth_mfa.router, "/ui/auth/mfa", "MFA"),
    (config_extra.router, "/ui", "Config"),
    (backup.router, "/ui", "Backup"),
    (danmaku_edit.router, "/ui", "Danmaku Edit"),
    (local_episode_group.router, "/ui", "Local Episode Group"),
    (poster.router, "/ui", "Poster"),
    (notification_routes.router, "/ui", "Notification"),
    (notification_routes.webhook_router, "", "Notification Webhook"),
    (anime_group.router, "/ui", "AnimeGroup"),
    (calendar.router, "/ui", "Calendar"),
    (cache.router, "/ui", "Cache"),
    (debug.router, "/ui", "Debug"),
    (health.router, "/ui", "Health"),
    (diagnostics.router, "/ui", "Diagnostics"),
    (data_check.router, "/ui", "DataCheck"),
    (recognition_check.router, "/ui", "RecognitionCheck"),
    (config_history.router, "/ui", "ConfigHistory"),
    (trends.router, "/ui", "Trends"),
    (audit.router, "/ui", "Audit"),
    (calendar_extra.router, "/ui", "CalendarExtra"),
    (ai_explain.router, "/ui", "AIExplain"),
    (scan_index.router, "/ui", "ScanIndex"),
    (subscriptions.router, "/ui", "Subscriptions"),
    (perf.router, "/ui", "Perf"),
    (assistant.router, "/ui", "Assistant"),
    (assistant_sessions.router, "/ui", "Assistant"),
    (webhook_api.router, "/webhook", "Webhook"),
):
    api_router.include_router(endpoint, prefix=prefix, tags=[tag], include_in_schema=False)
