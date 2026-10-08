"""
海报搜索与本地缓存相关的API端点
"""
import logging
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from src.utils.auth import security
from src.services.database_service import DatabaseService
from src.services.service_container import get_database_service
# 下载和作品关联均由编排层管理，API 不持有下载期间的事务。
from src.workflows.image_download import download_poster_to_local as download_poster_workflow

logger = logging.getLogger(__name__)

router = APIRouter()

# Fanart.tv 内置 API Key
FANART_API_KEY = "184e1a2b1fe3b94935365411f919f638"
FANART_BASE_URL = "https://webservice.fanart.tv/v3"


class DownloadPosterRequest(BaseModel):
    """下载海报到本地的请求"""
    imageUrl: str
    title: str
    season: int
    year: Optional[int] = None


class FanartPosterItem(BaseModel):
    """Fanart.tv 海报条目"""
    url: str
    lang: Optional[str] = None
    likes: int = 0


class FanartSearchResponse(BaseModel):
    """Fanart.tv 搜索响应"""
    posters: List[FanartPosterItem] = []
    source: str = "fanart.tv"


@router.get("/poster/local-image", summary="查找作品的本地海报路径")
async def get_local_image(
    title: str = Query(..., description="作品标题"),
    season: int = Query(..., description="季度"),
    year: Optional[int] = Query(None, description="年份"),
    current_user = Depends(security.get_current_user),
    db_service: DatabaseService = Depends(get_database_service)
):
    """根据标题、季度、年份查找对应 Anime 记录的 localImagePath。"""
    async with db_service.transaction():
        result = await db_service.anime_query.find_by_title_season_year(title, season, year)
    if not result:
        return {"localImagePath": None, "animeId": None}
    return {
        "localImagePath": result.get("localImagePath"),
        "animeId": result.get("id")
    }


@router.post("/poster/download-to-local", summary="下载网络海报到本地缓存")
async def download_poster_to_local(
    request_data: DownloadPosterRequest,
    current_user = Depends(security.get_current_user),
) -> dict:
    """委托编排层下载海报并关联作品，保留原响应字段。"""
    try:
        return await download_poster_workflow(
            request_data.imageUrl, request_data.title,
            request_data.season, request_data.year,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/poster/fanart", response_model=FanartSearchResponse, summary="从 Fanart.tv 搜索海报")
async def search_fanart_posters(
    tmdbId: Optional[str] = Query(None, description="TMDB ID（电影）"),
    tvdbId: Optional[str] = Query(None, description="TVDB ID（电视剧）"),
    mediaType: str = Query("tv", description="媒体类型: tv 或 movie"),
    current_user = Depends(security.get_current_user),
):
    """通过 TMDB ID 或 TVDB ID 从 Fanart.tv 获取海报列表。"""
    if not tmdbId and not tvdbId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="需要提供 tmdbId 或 tvdbId"
        )

    posters = []
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            if mediaType == "movie" and tmdbId:
                url = f"{FANART_BASE_URL}/movies/{tmdbId}?api_key={FANART_API_KEY}"
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    for item in data.get("movieposter", []):
                        posters.append(FanartPosterItem(
                            url=item.get("url", ""),
                            lang=item.get("lang"),
                            likes=int(item.get("likes", 0))
                        ))
            else:
                # 电视剧：优先用 tvdbId，其次用 tmdbId
                lookup_id = tvdbId or tmdbId
                url = f"{FANART_BASE_URL}/tv/{lookup_id}?api_key={FANART_API_KEY}"
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    for item in data.get("tvposter", []):
                        posters.append(FanartPosterItem(
                            url=item.get("url", ""),
                            lang=item.get("lang"),
                            likes=int(item.get("likes", 0))
                        ))

        # 按 likes 降序排序
        posters.sort(key=lambda x: x.likes, reverse=True)

    except httpx.RequestError as e:
        logger.warning(f"Fanart.tv 请求失败: {e}")
    except Exception as e:
        logger.error(f"Fanart.tv 搜索出错: {e}", exc_info=True)

    return FanartSearchResponse(posters=posters)

