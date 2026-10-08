"""
UI API端点模块
按功能模块组织的API路由

使用方式:
    # 导入路由模块
    from src.api.ui import anime, search, settings

    # 导入模型 (从 schemas 导入)
    from src.schemas.ui_models import UITaskResponse, UIProviderSearchResponse
"""

# 路由模块
# ⚠️ 临时注释掉需要重构的模块，以便应用能够启动
from . import (
    config, auth, scraper, metadata_source, media_server,
    source, subscriptions,
    # anime,  # ⚠️ 仍待重构
    # episode,  # ⚠️ 仍待重构
    # search,  # 已迁移缓存服务；保持现有路由加载方式
    # import_api,  # ⚠️ 需要重构 - 使用已废弃的 crud
    task,
    # token,  # ⚠️ 需要重构 - 使用已废弃的 crud
    config_extra,
    # settings,  # ⚠️ 需要重构 - 使用已废弃的 crud
    scheduled_task, webhook, system, auth_extra,
    local_danmaku, scraper_resources, parameters, danmaku_storage, backup, danmaku_edit,
    local_episode_group, poster, notification_routes, anime_group, auth_mfa, calendar,
    cache, debug,
    health, diagnostics, data_check, recognition_check, config_history,
    trends, audit, calendar_extra, ai_explain, scan_index,
    perf, assistant, assistant_sessions,
)

# ⚠️ models 模块已删除 - 请使用 from src.schemas.ui import ...

__all__ = [
    # 路由模块
    'config', 'auth', 'scraper', 'metadata_source', 'media_server',
    'source', 'subscriptions',
    # 'anime', 'episode', 'import_api', 'token', 'settings',  # ⚠️ 临时禁用需要重构的模块
    'task',
    'config_extra', 'scheduled_task', 'webhook', 'system', 'auth_extra',
    'local_danmaku', 'scraper_resources', 'parameters', 'danmaku_storage', 'backup', 'danmaku_edit',
    'local_episode_group', 'poster', 'notification_routes', 'anime_group', 'auth_mfa', 'calendar',
    'cache', 'debug',
    'health', 'diagnostics', 'data_check', 'recognition_check', 'config_history',
    'trends', 'audit', 'calendar_extra', 'ai_explain', 'scan_index',
    'perf', 'assistant', 'assistant_sessions',
]

