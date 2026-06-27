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


# A3 平台直链：bangumi-data 各站点 URL 模板（{{id}} 占位），用于把站点 id 拼成可点击链接
_BANGUMI_DATA_SITE_URL_TEMPLATES = {
    "bangumi": "https://bgm.tv/subject/{id}",
    "bilibili": "https://www.bilibili.com/bangumi/media/md{id}",
    "bilibili_hk_mo_tw": "https://www.bilibili.com/bangumi/media/md{id}",
    "acfun": "https://www.acfun.cn/bangumi/aa{id}",
    "iqiyi": "https://www.iqiyi.com/{id}.html",
    "youku": "https://list.youku.com/show/id_z{id}.html",
    "qq": "https://v.qq.com/detail/{id}.html",
    "mgtv": "https://www.mgtv.com/h/{id}.html",
    "netflix": "https://www.netflix.com/title/{id}",
    "tmdb": "https://www.themoviedb.org/{id}",
    "mal": "https://myanimelist.net/anime/{id}",
    "anidb": "https://anidb.net/anime/{id}",
    "crunchyroll": "https://www.crunchyroll.com/series/{id}",
    "prime": "https://www.amazon.co.jp/dp/{id}",
    "disneyplus": "https://www.disneyplus.com/series/-/{id}",
}


@router.get("/bangumi-data/platforms/{bangumi_id}", summary="A3：查询某番在各平台的 id/链接（bangumi-data 离线索引）")
async def get_bangumi_data_platforms(
    bangumi_id: str,
    current_user: models.User = Depends(security.get_current_user),
):
    """根据 bangumiId 从 bangumi-data 离线索引返回该作品在各平台的 id 与可点击链接。

    用途：让用户看到「这部番在 B站/爱奇艺/优酷/Netflix 等平台是否上架」并可跳转。
    注意：各平台 id 形态不一（如 tmdb 为 'movie/123'），是否能直接用于自动导入需逐平台适配，
    本端点只做映射展示，不驱动导入。
    """
    from src.services import get_bangumi_data_manager
    manager = get_bangumi_data_manager()
    if manager is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="bangumi-data 离线索引未就绪")

    sites_map = await manager.get_all_platform_ids(str(bangumi_id))
    if not sites_map:
        return {"bangumiId": bangumi_id, "platforms": []}

    platforms = []
    for site, sid in sites_map.items():
        template = _BANGUMI_DATA_SITE_URL_TEMPLATES.get(site)
        url = template.format(id=sid) if template else None
        platforms.append({"site": site, "id": sid, "url": url})
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
    current_user: models.User = Depends(security.get_current_user),
):
    """立即从 CDN 拉取 bangumi-data 并同步到本地索引（不必等定时任务）。

    注意：会拉取约 7MB 数据并全量重建索引表，耗时通常数秒到数十秒（取决于网络）。
    """
    from src.services import get_bangumi_data_manager
    manager = get_bangumi_data_manager()
    if manager is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="bangumi-data 管理器未就绪")
    result = await manager.sync()
    if not result.get("success"):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=result.get("message") or "同步失败")
    return {"message": f"同步完成，共 {result.get('count')} 条", "count": result.get("count")}

