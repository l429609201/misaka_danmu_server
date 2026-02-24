"""
外部控制API模块

使用方式:
    from src.api.control import control_router
"""

from fastapi import APIRouter, Depends

from .dependencies import verify_api_key

# 导入所有子路由
from .import_routes import router as import_router
from .library_routes import router as library_router
from .danmaku_routes import router as danmaku_router
from .token_routes import router as token_router
from .task_routes import router as task_router
from .scheduler_routes import router as scheduler_router
from .settings_routes import router as settings_router
from .rate_limit_routes import router as rate_limit_router
from .episode_group_routes import router as episode_group_router

# 创建主路由
control_router = APIRouter(
    tags=["External Control API"],
    dependencies=[Depends(verify_api_key)]
)

# 挂载所有子路由
control_router.include_router(import_router)
control_router.include_router(library_router)
control_router.include_router(danmaku_router)
control_router.include_router(token_router)
control_router.include_router(task_router)
control_router.include_router(scheduler_router)
control_router.include_router(settings_router)
control_router.include_router(rate_limit_router)
control_router.include_router(episode_group_router)

# 导出模型和依赖项供外部使用
from .models import (
    AutoImportSearchType, AutoImportMediaType,
    ControlActionResponse, ControlTaskResponse, ControlSearchResponse,
    ControlDirectImportRequest, ControlAnimeCreateRequest,
    ControlEditedImportRequest, ControlXmlImportRequest, ControlUrlImportRequest,
    DanmakuOutputSettings, ControlAnimeDetailsResponse, ControlAutoImportRequest,
    ControlMetadataSearchResponse
)

__all__ = [
    "control_router",
    # 模型
    "AutoImportSearchType", "AutoImportMediaType",
    "ControlActionResponse", "ControlTaskResponse", "ControlSearchResponse",
    "ControlDirectImportRequest", "ControlAnimeCreateRequest",
    "ControlEditedImportRequest", "ControlXmlImportRequest", "ControlUrlImportRequest",
    "DanmakuOutputSettings", "ControlAnimeDetailsResponse", "ControlAutoImportRequest",
    "ControlMetadataSearchResponse"
]
