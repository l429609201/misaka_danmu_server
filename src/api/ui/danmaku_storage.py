"""
弹幕存储管理API - 批量迁移、重命名、模板转换
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.utils.auth import security
from src.services.database_service import DatabaseService
from src.services.service_container import get_database_service
from src.services.config_service import ConfigService
from src.api.dependencies import get_config_service
from src.schemas.auth import User

router = APIRouter()
logger = logging.getLogger(__name__)


# ==================== 请求/响应模型 ====================

class BatchMigrateRequest(BaseModel):
    """批量迁移请求"""
    animeIds: List[int]
    targetPath: str
    keepStructure: bool = True
    conflictAction: str = "skip"  # skip, overwrite, rename


class DirectRenameItem(BaseModel):
    """直接重命名项"""
    episodeId: int
    newName: str


class BatchRenameRequest(BaseModel):
    """批量重命名请求"""
    animeIds: List[int]
    mode: str  # prefix, regex, direct
    prefix: Optional[str] = ""
    suffix: Optional[str] = ""
    regexPattern: Optional[str] = ""
    regexReplace: Optional[str] = ""
    directRenames: Optional[List[DirectRenameItem]] = None  # 直接指定新名称列表


class ApplyTemplateRequest(BaseModel):
    """应用模板请求"""
    animeIds: List[int]
    templateType: str  # tv, movie, id, plex, emby, custom
    customTemplate: Optional[str] = None  # 自定义模板字符串


class BatchOperationResult(BaseModel):
    """批量操作结果"""
    success: bool
    totalCount: int
    successCount: int
    failedCount: int
    skippedCount: int
    details: List[dict]


class MigratePreviewRequest(BaseModel):
    """迁移预览请求"""
    animeIds: List[int]
    targetPath: str
    keepStructure: bool = True


class MigratePreviewResult(BaseModel):
    """迁移预览结果"""
    totalCount: int
    previewItems: List[dict]


class TemplatePreviewRequest(BaseModel):
    """模板预览请求"""
    animeIds: List[int]
    templateType: str  # tv, movie, id, plex, emby, custom
    customTemplate: Optional[str] = None  # 自定义模板字符串


class TemplatePreviewResult(BaseModel):
    """模板预览结果"""
    totalCount: int
    previewItems: List[dict]


class RenamePreviewRequest(BaseModel):
    """重命名预览请求"""
    animeIds: List[int]
    mode: str  # prefix, regex
    prefix: Optional[str] = ""
    suffix: Optional[str] = ""
    regexPattern: Optional[str] = ""
    regexReplace: Optional[str] = ""


class RenamePreviewResult(BaseModel):
    """重命名预览结果"""
    totalCount: int
    previewItems: List[dict]


# ==================== API端点 ====================

# 模板变量定义 - 集中管理，方便扩展
# 模板变量定义 - 统一列表，电影类型使用季/集时输出null
TEMPLATE_VARIABLES = [
    {"name": "${title}", "desc": "作品标题", "example": "葬送的芙莉莲"},
    {"name": "${titleBase}", "desc": "标准化标题(去除季度信息)", "example": "葬送的芙莉莲"},
    {"name": "${season}", "desc": "季度号(电影为null)", "example": "1"},
    {"name": "${season:02d}", "desc": "季度号补零(电影为null)", "example": "01"},
    {"name": "${episode}", "desc": "集数(电影为null)", "example": "1"},
    {"name": "${episode:02d}", "desc": "集数补零(电影为null)", "example": "01"},
    {"name": "${episode:03d}", "desc": "集数补零到3位(电影为null)", "example": "001"},
    {"name": "${year}", "desc": "年份", "example": "2023"},
    {"name": "${provider}", "desc": "数据源名称", "example": "bilibili"},
    {"name": "${animeId}", "desc": "作品ID", "example": "234"},
    {"name": "${episodeId}", "desc": "分集ID", "example": "25000234010001"},
    {"name": "${sourceId}", "desc": "数据源ID", "example": "1"},
    {"name": "${tmdbId}", "desc": "TMDB ID（未关联时为空）", "example": "209867"},
]


@router.get("/template-variables", summary="获取模板变量列表")
async def get_template_variables(
    current_user: User = Depends(security.get_current_user)
):
    """
    获取所有可用的模板变量列表，用于前端动态渲染变量按钮。
    返回统一的变量列表，电影类型使用季/集变量时会输出null。
    """
    return TEMPLATE_VARIABLES


@router.post("/preview-migrate", response_model=MigratePreviewResult, summary="预览批量迁移")
async def preview_migrate(
    request: MigratePreviewRequest,
    current_user: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service)
):
    """预览迁移结果，不实际执行"""
    result = await crud.preview_migrate_danmaku(
        session,
        anime_ids=request.animeIds,
        target_path=request.targetPath,
        keep_structure=request.keepStructure
    )
    return MigratePreviewResult(**result)

@router.post("/batch-migrate", response_model=BatchOperationResult, summary="批量迁移弹幕文件")
async def batch_migrate(
    request: BatchMigrateRequest,
    current_user: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service)
):
    """将选中条目的弹幕文件迁移到新目录"""
    result = await crud.batch_migrate_danmaku(
        session,
        anime_ids=request.animeIds,
        target_path=request.targetPath,
        keep_structure=request.keepStructure,
        conflict_action=request.conflictAction
    )
    return BatchOperationResult(**result)


@router.post("/preview-rename", response_model=RenamePreviewResult, summary="预览批量重命名")
async def preview_rename(
    request: RenamePreviewRequest,
    current_user: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service)
):
    """预览重命名结果，不实际执行"""
    result = await crud.preview_rename_danmaku(
        session,
        anime_ids=request.animeIds,
        mode=request.mode,
        prefix=request.prefix or "",
        suffix=request.suffix or "",
        regex_pattern=request.regexPattern or "",
        regex_replace=request.regexReplace or ""
    )
    return RenamePreviewResult(**result)


@router.post("/batch-rename", response_model=BatchOperationResult, summary="批量重命名弹幕文件")
async def batch_rename(
    request: BatchRenameRequest,
    current_user: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service)
):
    """批量重命名选中条目的弹幕文件"""
    # 转换直接重命名列表格式
    direct_renames = None
    if request.directRenames:
        direct_renames = {item.episodeId: item.newName for item in request.directRenames}

    result = await crud.batch_rename_danmaku(
        session,
        anime_ids=request.animeIds,
        mode=request.mode,
        prefix=request.prefix or "",
        suffix=request.suffix or "",
        regex_pattern=request.regexPattern or "",
        regex_replace=request.regexReplace or "",
        direct_renames=direct_renames
    )
    return BatchOperationResult(**result)


@router.post("/preview-template", response_model=TemplatePreviewResult, summary="预览应用模板")
async def preview_template(
    request: TemplatePreviewRequest,
    current_user: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service),
    config_service: ConfigService = Depends(get_config_service)
):
    """预览模板应用结果，不实际执行"""
    result = await crud.preview_apply_template(
        session,
        anime_ids=request.animeIds,
        template_type=request.templateType,
        custom_template=request.customTemplate,
        config_service=config_service
    )
    return TemplatePreviewResult(**result)


@router.post("/apply-template", response_model=BatchOperationResult, summary="应用新模板")
async def apply_template(
    request: ApplyTemplateRequest,
    current_user: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service),
    config_service: ConfigService = Depends(get_config_service)
):
    """按新的存储模板重新组织弹幕文件"""
    result = await crud.apply_danmaku_template(
        session,
        anime_ids=request.animeIds,
        template_type=request.templateType,
        custom_template=request.customTemplate,
        config_service=config_service
    )
    return BatchOperationResult(**result)

