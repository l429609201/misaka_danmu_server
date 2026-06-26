from fastapi import APIRouter

from src.api.webhook_api import router as webhook_router
from src.api.control import control_router
from src.api.ui import (
    auth, scraper, metadata_source, media_server,
    anime, source, episode, search, import_api, task,
    token, config_extra, settings, scheduled_task, webhook, system, auth_extra,
    local_danmaku, scraper_resources, parameters, danmaku_storage, backup, danmaku_edit,
    local_episode_group, poster, notification_routes, anime_group, auth_mfa, calendar,
    cache, debug,
    health, diagnostics, data_check, recognition_check, config_history,
    trends, audit, calendar_extra, ai_explain, scan_index,
    subscriptions,
)

# This router aggregates all non-dandanplay API endpoints.
api_router = APIRouter()

# 基础端点: Auth, Scraper, Metadata Source, Media Server, Local Danmaku
api_router.include_router(auth.router, prefix="/ui/auth", tags=["Auth"], include_in_schema=False)
api_router.include_router(scraper.router, prefix="/ui", tags=["Scraper"], include_in_schema=False)
api_router.include_router(scraper_resources.router, prefix="/ui", tags=["Scraper Resources"], include_in_schema=False)
api_router.include_router(parameters.router, prefix="/ui", tags=["Parameters"], include_in_schema=False)
api_router.include_router(metadata_source.router, prefix="/ui", tags=["Metadata Source"], include_in_schema=False)
api_router.include_router(media_server.router, prefix="/ui", tags=["Media Server"], include_in_schema=False)
api_router.include_router(local_danmaku.router, prefix="/ui", tags=["Local Danmaku"], include_in_schema=False)
api_router.include_router(danmaku_storage.router, prefix="/ui/danmaku-storage", tags=["Danmaku Storage"], include_in_schema=False)

# 新增的模块化端点 - 第1批: Anime, Source, Episode
api_router.include_router(anime.router, prefix="/ui", tags=["Anime"], include_in_schema=False)
api_router.include_router(source.router, prefix="/ui", tags=["Source"], include_in_schema=False)
api_router.include_router(episode.router, prefix="/ui", tags=["Episode"], include_in_schema=False)

# 新增的模块化端点 - 第2批: Search, Import, Task
api_router.include_router(search.router, prefix="/ui", tags=["Search"], include_in_schema=False)
api_router.include_router(import_api.router, prefix="/ui", tags=["Import"], include_in_schema=False)
api_router.include_router(task.router, prefix="/ui", tags=["Task"], include_in_schema=False)

# 新增的模块化端点 - 第3批: Token, Settings, ScheduledTask, Webhook, System, Auth
api_router.include_router(token.router, prefix="/ui", tags=["Token"], include_in_schema=False)
api_router.include_router(settings.router, prefix="/ui", tags=["Settings"], include_in_schema=False)
api_router.include_router(scheduled_task.router, prefix="/ui", tags=["ScheduledTask"], include_in_schema=False)
api_router.include_router(webhook.router, prefix="/ui", tags=["Webhook"], include_in_schema=False)
api_router.include_router(system.router, prefix="/ui", tags=["System"], include_in_schema=False)
api_router.include_router(auth_extra.auth_router, prefix="/ui/auth", tags=["Auth"], include_in_schema=False)

# MFA 端点 (TOTP 两步验证 + PassKey)
api_router.include_router(auth_mfa.router, prefix="/ui/auth/mfa", tags=["MFA"], include_in_schema=False)

# Config端点 - config_extra.router 已包含所有config路由,不再需要 config.router
# config.router 的通配符路由 /{config_key} 会与其他路由冲突,已在 config_extra.router 中重新实现
api_router.include_router(config_extra.router, prefix="/ui", tags=["Config"], include_in_schema=False)

# 备份管理端点
api_router.include_router(backup.router, prefix="/ui", tags=["Backup"], include_in_schema=False)

# 弹幕编辑端点
api_router.include_router(danmaku_edit.router, prefix="/ui", tags=["Danmaku Edit"], include_in_schema=False)

# 本地剧集组端点
api_router.include_router(local_episode_group.router, prefix="/ui", tags=["Local Episode Group"], include_in_schema=False)

# 海报搜索端点
api_router.include_router(poster.router, prefix="/ui", tags=["Poster"], include_in_schema=False)

# 通知渠道端点
api_router.include_router(notification_routes.router, prefix="/ui", tags=["Notification"], include_in_schema=False)
# Webhook 回调端点
api_router.include_router(notification_routes.webhook_router, tags=["Notification Webhook"], include_in_schema=False)

# 弹幕库分组端点
api_router.include_router(anime_group.router, prefix="/ui", tags=["AnimeGroup"], include_in_schema=False)

# 日历视图端点
api_router.include_router(calendar.router, prefix="/ui", tags=["Calendar"], include_in_schema=False)

# 缓存管理端点
api_router.include_router(cache.router, prefix="/ui", tags=["Cache"], include_in_schema=False)

# 调试工具端点
api_router.include_router(debug.router, prefix="/ui", tags=["Debug"], include_in_schema=False)

# 系统健康度 / 弹幕源健康度 / 配置完整性 / 番剧优先级
api_router.include_router(health.router, prefix="/ui", tags=["Health"], include_in_schema=False)

# 诊断中心（日志诊断 + 环境诊断）
api_router.include_router(diagnostics.router, prefix="/ui", tags=["Diagnostics"], include_in_schema=False)

# 数据体检 / 映射修复
api_router.include_router(data_check.router, prefix="/ui", tags=["DataCheck"], include_in_schema=False)

# 识别词规则冲突检测
api_router.include_router(recognition_check.router, prefix="/ui", tags=["RecognitionCheck"], include_in_schema=False)

# 配置变更历史 / 回滚
api_router.include_router(config_history.router, prefix="/ui", tags=["ConfigHistory"], include_in_schema=False)

# 任务画像 / 容量趋势
api_router.include_router(trends.router, prefix="/ui", tags=["Trends"], include_in_schema=False)

# 安全审计
api_router.include_router(audit.router, prefix="/ui", tags=["Audit"], include_in_schema=False)

# 日程增强
api_router.include_router(calendar_extra.router, prefix="/ui", tags=["CalendarExtra"], include_in_schema=False)

# AI匹配可解释性
api_router.include_router(ai_explain.router, prefix="/ui", tags=["AIExplain"], include_in_schema=False)

# 本地扫描增量索引
api_router.include_router(scan_index.router, prefix="/ui", tags=["ScanIndex"], include_in_schema=False)

# 通用订阅助手（订阅源能力探测 + 订阅目标/候选项）
api_router.include_router(subscriptions.router, prefix="/ui", tags=["Subscriptions"], include_in_schema=False)

api_router.include_router(webhook_router, prefix="/webhook", tags=["Webhook"], include_in_schema=False)

# 注意：dandan_router 在 main.py 中被单独处理，因为它的路径结构
# (/api/v1/{token}) 与其他嵌套在 /api 前缀下的路由 (例如 /api/ui, /api/tmdb) 不同，
# 需要位于 /api 前缀的根部。