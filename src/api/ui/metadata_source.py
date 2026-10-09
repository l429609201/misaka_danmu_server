"""
元数据源(Metadata Source)相关的API端点
"""

import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Body, status

from src.schemas.auth import User
from src.schemas.metadata import MetadataSourceStatusResponse, MetadataDetailsResponse
from src.schemas.control import MetadataSourceSettingUpdate
from src.utils.auth import security
from src.services.metadata_service import MetadataService
from src.services.task_manager import TaskManager
from src.services.scraper_manager import ScraperManager
from src.api.dependencies import get_metadata_service, get_task_manager, get_scraper_manager
from src.workflows.calendar.subscription_flow import update_metadata_provider_config
from src.workflows.bangumi_data_tasks import submit_bangumi_data_task
from src.workflows.bangumi_data_platforms import resolve_bangumi_danmaku_sources

router = APIRouter()
logger = logging.getLogger(__name__)


def build_metadata_source_router(metadata_service: MetadataService) -> APIRouter:
    """在 HTTP 组合层聚合来源公开的子路由。"""
    source_router = APIRouter()
    for provider, source in metadata_service.sources.items():
        api_router = getattr(source, "api_router", None)
        if isinstance(api_router, APIRouter):
            source_router.include_router(
                api_router, prefix=f"/{provider}",
                tags=[f"Metadata - {provider.capitalize()}"],
            )
    return source_router


@router.get("/metadata-sources", response_model=List[MetadataSourceStatusResponse], summary="获取所有元数据源的设置")
async def get_metadata_source_settings(
    current_user: User = Depends(security.get_current_user),
    manager: MetadataService = Depends(get_metadata_service)
):
    """获取所有元数据源及其当前状态(配置、连接性等)"""
    return await manager.get_sources_with_status()


@router.put("/metadata-sources", status_code=status.HTTP_204_NO_CONTENT, summary="更新元数据源的设置")
async def update_metadata_source_settings(
    settings: List[MetadataSourceSettingUpdate],
    current_user: User = Depends(security.get_current_user),
    manager: MetadataService = Depends(get_metadata_service)
):
    """批量更新元数据源的启用状态、辅助搜索状态和显示顺序"""
    await manager.update_source_settings(settings)
    logger.info(f"用户 '{current_user.username}' 更新了元数据源设置,已重新加载。")


@router.get("/metadata-sources/{providerName}/config", response_model=Dict[str, Any], summary="获取指定元数据源的配置")
async def get_metadata_source_config(
    providerName: str,
    current_user: User = Depends(security.get_current_user),
    metadata_manager: MetadataService = Depends(get_metadata_service)
):
    """获取单个元数据源的详细配置"""
    try:
        return await metadata_manager.getProviderConfig(providerName)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.put("/metadata-sources/{providerName}/config", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定元数据源的配置")
async def update_metadata_source_config(
    providerName: str,
    payload: Dict[str, Any],
    current_user: User = Depends(security.get_current_user),
    metadata_manager: MetadataService = Depends(get_metadata_service)
):
    """更新指定元数据源的配置"""
    try:
        await update_metadata_provider_config(metadata_manager, providerName, payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"更新元数据源 '{providerName}' 配置时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="更新配置时发生内部错误。")


@router.get("/metadata/{provider}/search", response_model=List[MetadataDetailsResponse], summary="从元数据源搜索")
async def search_metadata(
    provider: str,
    keyword: str,
    mediaType: Optional[str] = Query(None),
    current_user: User = Depends(security.get_current_user),
    manager: MetadataService = Depends(get_metadata_service)
):
    """从指定元数据源搜索内容"""
    return await manager.search(provider, keyword, current_user, mediaType=mediaType)


@router.get("/metadata/{provider}/details/{item_id}", response_model=MetadataDetailsResponse, summary="获取元数据详情")
async def get_metadata_details(
    provider: str,
    item_id: str,
    mediaType: Optional[str] = Query(None),
    current_user: User = Depends(security.get_current_user),
    manager: MetadataService = Depends(get_metadata_service)
):
    """获取指定元数据源的详情"""
    details = await manager.get_details(provider, item_id, current_user, mediaType=mediaType)
    if not details:
        raise HTTPException(status_code=404, detail="未找到详情")
    return details


@router.get("/metadata/{provider}/details/{mediaType}/{item_id}", response_model=MetadataDetailsResponse, summary="获取元数据详情 (带媒体类型)", include_in_schema=False)
async def get_metadata_details_with_type(
    provider: str,
    mediaType: str,
    item_id: str,
    current_user: User = Depends(security.get_current_user),
    manager: MetadataService = Depends(get_metadata_service)
):
    """
    一个兼容性路由,允许将 mediaType 作为路径的一部分
    """
    details = await manager.get_details(provider, item_id, current_user, mediaType=mediaType)
    if not details:
        raise HTTPException(status_code=404, detail="未找到详情")
    return details


@router.post("/metadata/{provider}/actions/{action_name}", summary="执行元数据源的自定义操作")
async def execute_metadata_action(
    provider: str,
    action_name: str,
    request: Request,
    payload: Optional[Dict[str, Any]] = Body(None),
    current_user: User = Depends(security.get_current_user),
    manager: MetadataService = Depends(get_metadata_service)
):
    """执行指定元数据源的自定义操作"""
    try:
        return await manager.execute_action(provider, action_name, payload or {}, current_user, request=request)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/bangumi-data/platforms/{bangumi_id}", summary="A3：查询某番在各平台的 id/链接（bangumi-data 离线索引）")
async def get_bangumi_data_platforms(
    bangumi_id: str,
    current_user: User = Depends(security.get_current_user),
    metadata_service: MetadataService = Depends(get_metadata_service),
):
    """根据 bangumiId 从 bangumi-data 离线索引返回该作品在各平台的 id 与可点击链接。

    用途：让用户看到「这部番在 B站/爱奇艺/优酷/Netflix 等平台是否上架」并可跳转。
    注意：URL 由随 data.json 动态下发的 siteMeta.urlTemplate 拼成（不再硬编码），各平台 id 形态不一
    （如 tmdb 为 'tv/123'），是否能直接用于自动导入需逐平台适配，本端点只做映射展示。
    """
    platforms = await metadata_service.get_bangumi_platform_urls(str(bangumi_id))
    return {"bangumiId": bangumi_id, "platforms": platforms}


@router.get("/bangumi-data/status", summary="查询 bangumi-data 离线索引状态（条目数）")
async def get_bangumi_data_status(
    current_user: User = Depends(security.get_current_user),
    metadata_service: MetadataService = Depends(get_metadata_service),
):
    """返回当前 bangumi-data 离线索引是否可用及条目数。"""
    return await metadata_service.get_bangumi_data_status()


@router.post("/bangumi-data/sync", summary="手动触发 bangumi-data 离线索引同步")
async def trigger_bangumi_data_sync(
    current_user: User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """提交后台同步任务并返回任务标识。"""
    task_id = await submit_bangumi_data_task(task_manager, "sync")
    return {"message": "bangumi-data 同步任务已提交", "taskId": task_id}


@router.post("/bangumi-data/clear", summary="清除 bangumi-data 离线索引数据")
async def trigger_bangumi_data_clear(
    current_user: User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """提交后台离线索引清理任务。"""
    task_id = await submit_bangumi_data_task(task_manager, "clear")
    return {"message": "bangumi-data 清除任务已提交", "taskId": task_id}


@router.get("/bangumi-data/danmaku-sources/{bangumi_id}", summary="反向解析：某番各平台 URL 及是否有对应弹幕源")
async def get_bangumi_data_danmaku_sources(
    bangumi_id: str,
    current_user: User = Depends(security.get_current_user),
    metadata_service: MetadataService = Depends(get_metadata_service),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
):
    """返回各平台链接及对应弹幕源可用性。"""
    return await resolve_bangumi_danmaku_sources(bangumi_id, metadata_service, scraper_manager)

