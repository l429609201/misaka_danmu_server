"""
UI API端点模块
按功能模块组织的API路由

使用方式:
    # 导入路由模块
    from src.api.ui import anime, search, settings

    # 导入模型 (聚合导入)
    from src.api.ui import models
    response = models.UITaskResponse(message="ok", taskId="123")

    # 或直接导入具体模型
    from src.api.ui.models import UITaskResponse, UIProviderSearchResponse
"""

# 路由模块
from . import (
    config, auth, scraper, metadata_source, media_server,
    anime, source, episode, search, import_api, task,
    token, config_extra, settings, scheduled_task, webhook, system, auth_extra,
    local_danmaku, scraper_resources, parameters, danmaku_storage, backup, danmaku_edit,
    local_episode_group, poster, notification_routes
)

# 模型模块 - 支持 from src.api.ui import models 风格
from . import models

__all__ = [
    # 路由模块
    'config', 'auth', 'scraper', 'metadata_source', 'media_server',
    'anime', 'source', 'episode', 'search', 'import_api', 'task',
    'token', 'config_extra', 'settings', 'scheduled_task', 'webhook', 'system', 'auth_extra',
    'local_danmaku', 'scraper_resources', 'parameters', 'danmaku_storage', 'backup', 'danmaku_edit',
    'local_episode_group', 'poster', 'notification_routes',
    # 模型
    'models',
]

