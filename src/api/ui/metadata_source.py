"""
元数据源(Metadata Source)相关的API端点
"""

import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Body, status
from pydantic import BaseModel

from src.db import models
from src import security
from src.services import MetadataSourceManager
from src.api.dependencies import get_metadata_manager

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/metadata-sources", response_model=List[models.MetadataSourceStatusResponse], summary="获取所有元数据源的设置")
async def get_metadata_source_settings(
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """获取所有元数据源及其当前状态(配置、连接性等)"""
    return await manager.get_sources_with_status()


@router.put("/metadata-sources", status_code=status.HTTP_204_NO_CONTENT, summary="更新元数据源的设置")
async def update_metadata_source_settings(
    settings: List[models.MetadataSourceSettingUpdate],
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """批量更新元数据源的启用状态、辅助搜索状态和显示顺序"""
    await manager.update_source_settings(settings)
    logger.info(f"用户 '{current_user.username}' 更新了元数据源设置,已重新加载。")


@router.get("/metadata-sources/{providerName}/config", response_model=Dict[str, Any], summary="获取指定元数据源的配置")
async def get_metadata_source_config(
    providerName: str,
    current_user: models.User = Depends(security.get_current_user),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
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
    current_user: models.User = Depends(security.get_current_user),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """更新指定元数据源的配置"""
    try:
        await metadata_manager.updateProviderConfig(providerName, payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"更新元数据源 '{providerName}' 配置时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="更新配置时发生内部错误。")


@router.get("/metadata/{provider}/search", response_model=List[models.MetadataDetailsResponse], summary="从元数据源搜索")
async def search_metadata(
    provider: str,
    keyword: str,
    mediaType: Optional[str] = Query(None),
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """从指定元数据源搜索内容"""
    return await manager.search(provider, keyword, current_user, mediaType=mediaType)


@router.get("/metadata/{provider}/details/{item_id}", response_model=models.MetadataDetailsResponse, summary="获取元数据详情")
async def get_metadata_details(
    provider: str,
    item_id: str,
    mediaType: Optional[str] = Query(None),
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """获取指定元数据源的详情"""
    details = await manager.get_details(provider, item_id, current_user, mediaType=mediaType)
    if not details:
        raise HTTPException(status_code=404, detail="未找到详情")
    return details


@router.get("/metadata/{provider}/details/{mediaType}/{item_id}", response_model=models.MetadataDetailsResponse, summary="获取元数据详情 (带媒体类型)", include_in_schema=False)
async def get_metadata_details_with_type(
    provider: str,
    mediaType: str,
    item_id: str,
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
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
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """执行指定元数据源的自定义操作"""
    try:
        return await manager.execute_action(provider, action_name, payload or {}, current_user, request=request)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/bangumi-data/platforms/{bangumi_id}", summary="A3：查询某番在各平台的 id/链接（bangumi-data 离线索引）")
async def get_bangumi_data_platforms(
    bangumi_id: str,
    current_user: models.User = Depends(security.get_current_user),
):
    """根据 bangumiId 从 bangumi-data 离线索引返回该作品在各平台的 id 与可点击链接。

    用途：让用户看到「这部番在 B站/爱奇艺/优酷/Netflix 等平台是否上架」并可跳转。
    注意：URL 由随 data.json 动态下发的 siteMeta.urlTemplate 拼成（不再硬编码），各平台 id 形态不一
    （如 tmdb 为 'tv/123'），是否能直接用于自动导入需逐平台适配，本端点只做映射展示。
    """
    from src.services import get_bangumi_data_manager
    manager = get_bangumi_data_manager()
    if manager is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="bangumi-data 离线索引未就绪")

    # 复用反向解析器：动态 siteMeta 拼 URL
    platforms = await manager.build_platform_urls(str(bangumi_id))
    return {"bangumiId": bangumi_id, "platforms": platforms}


@router.get("/bangumi-data/status", summary="查询 bangumi-data 离线索引状态（条目数）")
async def get_bangumi_data_status(
    current_user: models.User = Depends(security.get_current_user),
):
    """返回当前 bangumi-data 离线索引的条目数，用于判断是否已同步。"""
    from src.services import get_bangumi_data_manager
    manager = get_bangumi_data_manager()
    if manager is None:
        return {"ready": False, "count": 0}
    count = await manager.count()
    return {"ready": True, "count": count}


@router.post("/bangumi-data/sync", summary="手动触发 bangumi-data 离线索引同步")
async def trigger_bangumi_data_sync(
    request: Request,
    current_user: models.User = Depends(security.get_current_user),
):
    """提交一个后台任务，从 CDN 拉取 bangumi-data 并同步到本地索引（不必等定时任务）。

    why：原实现直接 await manager.sync() 会阻塞 HTTP 请求数秒~数十秒且无任务记录，
    改为走任务管理器，与定时同步(BangumiDataSyncJob)保持一致：有进度、有历史、不阻塞接口。
    """
    from src.services import get_bangumi_data_manager, TaskSuccess

    manager = get_bangumi_data_manager()
    if manager is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="bangumi-data 管理器未就绪")

    task_manager = request.app.state.task_manager

    async def _sync_coro(session, progress_callback):
        await progress_callback(10, "正在从 CDN 拉取 bangumi-data...")
        result = await manager.sync()
        if result.get("success"):
            raise TaskSuccess(f"bangumi-data 同步完成，共 {result.get('count')} 条。")
        raise TaskSuccess(f"bangumi-data 同步失败：{result.get('message')}")

    # unique_key 防止重复提交（与定时任务共用语义前缀，便于去重检测）
    task_id, _ = await task_manager.submit_task(
        _sync_coro,
        "bangumi-data 离线索引同步（手动）",
        unique_key="bangumi-data-sync-manual",
        task_type="bangumiDataSync",
        queue_type="management",
    )
    return {"message": "bangumi-data 同步任务已提交", "taskId": task_id}


@router.post("/bangumi-data/clear", summary="清除 bangumi-data 离线索引数据")
async def trigger_bangumi_data_clear(
    request: Request,
    current_user: models.User = Depends(security.get_current_user),
):
    """提交一个后台任务，清空本地 bangumi-data 离线索引表。

    why：与立即同步保持一致走任务管理器，有任务记录、不阻塞接口（清表本身虽快，但统一入口便于审计）。
    """
    from src.services import get_bangumi_data_manager, TaskSuccess

    manager = get_bangumi_data_manager()
    if manager is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="bangumi-data 管理器未就绪")

    task_manager = request.app.state.task_manager

    async def _clear_coro(session, progress_callback):
        await progress_callback(10, "正在清除 bangumi-data 离线索引...")
        result = await manager.clear()
        raise TaskSuccess(f"bangumi-data 离线索引已清除，共 {result.get('count')} 条。")

    task_id, _ = await task_manager.submit_task(
        _clear_coro,
        "bangumi-data 离线索引清除",
        unique_key="bangumi-data-clear-manual",
        task_type="bangumiDataClear",
        queue_type="management",
    )
    return {"message": "bangumi-data 清除任务已提交", "taskId": task_id}


@router.get("/bangumi-data/danmaku-sources/{bangumi_id}", summary="反向解析：某番各平台 URL 及是否有对应弹幕源")
async def get_bangumi_data_danmaku_sources(
    bangumi_id: str,
    request: Request,
    current_user: models.User = Depends(security.get_current_user),
):
    """把某番在各平台的 id 反向拼成官方 URL，并探测每个 URL 是否有「已实现的弹幕源」可直接抓弹幕。

    工作链路：bangumiId → sites{平台:id} → siteMeta.urlTemplate 拼 URL
            → scraper_manager.get_scraper_by_domain(url) 判定能否抓弹幕。
    available=true 的平台可走 /extcomment 直接获取弹幕。
    """
    from src.services import get_bangumi_data_manager
    manager = get_bangumi_data_manager()
    if manager is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="bangumi-data 离线索引未就绪")

    platforms = await manager.build_platform_urls(str(bangumi_id))
    scraper_manager = getattr(request.app.state, "scraper_manager", None)

    sources = []
    for p in platforms:
        url = p.get("url")
        provider = None
        available = False
        # 仅当能拼出 URL 且存在处理该域名的弹幕源时，标记为可抓取
        if url and scraper_manager is not None:
            scraper = scraper_manager.get_scraper_by_domain(url)
            if scraper is not None:
                provider = scraper.provider_name
                available = True
        sources.append({
            "site": p.get("site"),
            "id": p.get("id"),
            "title": p.get("title"),
            "type": p.get("type"),
            "url": url,
            "provider": provider,      # 对应的弹幕源 provider_name（无则 None）
            "available": available,    # 是否可直接通过该 URL 抓弹幕
        })
    return {"bangumiId": bangumi_id, "sources": sources}

