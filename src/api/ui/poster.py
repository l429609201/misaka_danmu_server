"""
海报搜索与本地缓存相关的API端点
"""
import logging
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src import security
from src.db import crud, models, orm_models, get_db_session
from src.services import ScraperManager
from src.utils import download_image

from src.api.dependencies import get_scraper_manager

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
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """根据标题、季度、年份查找对应 Anime 记录的 localImagePath。"""
    result = await crud.find_anime_by_title_season_year(session, title, season, year)
    if not result:
        return {"localImagePath": None, "animeId": None}
    return {
        "localImagePath": result.get("localImagePath"),
        "animeId": result.get("id")
    }


@router.post("/poster/download-to-local", summary="下载网络海报到本地缓存")
async def download_poster_to_local(
    request_data: DownloadPosterRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager)
):
    """将网络图片URL下载到本地缓存，并更新对应 Anime 记录的 localImagePath。"""
    # 1. 下载图片
    new_local_path = await download_image(request_data.imageUrl, session, scraper_manager)
    if not new_local_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图片下载失败，请检查URL或服务器日志。"
        )

    # 2. 查找对应的 Anime 记录
    anime_result = await crud.find_anime_by_title_season_year(
        session, request_data.title, request_data.season, request_data.year
    )

    anime_id = None
    if anime_result:
        anime_id = anime_result.get("id")
        # 3. 更新 Anime 的 imageUrl 和 localImagePath
        stmt = (
            update(orm_models.Anime)
            .where(orm_models.Anime.id == anime_id)
            .values(imageUrl=request_data.imageUrl, localImagePath=new_local_path)
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(f"已更新 Anime ID={anime_id} 的海报: {new_local_path}")
    else:
        logger.warning(f"未找到匹配的 Anime 记录 (title={request_data.title}, season={request_data.season})，图片已下载但未关联")

    return {
        "localImagePath": new_local_path,
        "animeId": anime_id
    }


@router.get("/poster/fanart", response_model=FanartSearchResponse, summary="从 Fanart.tv 搜索海报")
async def search_fanart_posters(
    tmdbId: Optional[str] = Query(None, description="TMDB ID（电影）"),
    tvdbId: Optional[str] = Query(None, description="TVDB ID（电视剧）"),
    mediaType: str = Query("tv", description="媒体类型: tv 或 movie"),
    current_user: models.User = Depends(security.get_current_user),
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

