import logging
from typing import Any, Dict, List, Optional, Set
from datetime import datetime, timedelta
import re
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from .. import crud, models, security
from ..database import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter()

# --- TVDB Token Management ---
# 使用一个简单的模块级缓存来存储TVDB的JWT令牌
_tvdb_token_cache: Dict[str, Any] = {"token": None, "expires_at": datetime.utcnow()}

async def _create_tvdb_client(session: AsyncSession) -> httpx.AsyncClient:
    """Non-FastAPI dependent version of get_tvdb_client."""
    proxy_url_task = crud.get_config_value(session, "proxy_url", "")
    proxy_enabled_globally_task = crud.get_config_value(session, "proxy_enabled", "false")
    metadata_settings_task = crud.get_all_metadata_source_settings(session)

    proxy_url, proxy_enabled_str, metadata_settings = await asyncio.gather(
        proxy_url_task, proxy_enabled_globally_task, metadata_settings_task
    )
    proxy_enabled_globally = proxy_enabled_str.lower() == 'true'

    provider_setting = next((s for s in metadata_settings if s['providerName'] == 'tvdb'), None)
    use_proxy_for_this_provider = provider_setting.get('use_proxy', False) if provider_setting else False

    proxy_to_use = proxy_url if proxy_enabled_globally and use_proxy_for_this_provider and proxy_url else None
    
    client = httpx.AsyncClient(base_url="https://api4.thetvdb.com/v4", timeout=20.0, proxy=proxy_to_use)
    token = await get_tvdb_token(session, client)
    client.headers.update(
        {"Authorization": f"Bearer {token}", "User-Agent": "DanmuApiServer/1.0"}
    )
    return client


async def get_tvdb_token(session: AsyncSession, client: httpx.AsyncClient) -> str:
    """获取一个有效的TVDB令牌，如果需要则刷新。"""
    global _tvdb_token_cache
    # 如果缓存中的token有效，则直接返回
    if _tvdb_token_cache["token"] and _tvdb_token_cache["expires_at"] > datetime.utcnow():
        return _tvdb_token_cache["token"]

    logger.info("TVDB token 已过期或未找到，正在请求新的令牌。")
    api_key = await crud.get_config_value(session, "tvdb_api_key", "")

    if not api_key:
        raise ValueError("TVDB API Key 未配置。")
    try:
        # TVDB V4 API 的登录端点
        response = await client.post("/login", json={"apikey": api_key})
        response.raise_for_status()
        # 根据TVDB API v4文档，token在 'data' 字段下
        token = response.json().get("data", {}).get("token")
        if not token:
            raise ValueError("登录响应中未包含令牌。")

        # 令牌有效期为24小时，我们设置一个23小时的缓存
        _tvdb_token_cache["token"] = token
        _tvdb_token_cache["expires_at"] = datetime.utcnow() + timedelta(hours=23)
        logger.info("成功获取新的TVDB令牌。")
        return token
    except Exception as e:
        logger.error(f"获取TVDB令牌失败: {e}", exc_info=True)
        raise ValueError("TVDB认证失败。")


async def get_tvdb_client(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> httpx.AsyncClient:
    """依赖项：获取一个经过认证的TVDB客户端。"""
    try:
        return await _create_tvdb_client(session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# --- Pydantic Models for TVDB ---
class TvdbSearchResult(BaseModel):
    tvdb_id: str
    name: str
    image_url: Optional[str] = None
    overview: Optional[str] = None
    year: Optional[str] = None
    type: str

class TvdbSearchResponse(BaseModel):
    data: List[TvdbSearchResult]

class TvdbAlias(BaseModel):
    language: str
    name: str

class TvdbApiSearchResult(BaseModel):
    id: str
    title: str
    details: str
    imageUrl: Optional[str] = None

class TvdbRemoteId(BaseModel):
    id: str
    type: int
    sourceName: str

class TvdbDetailsResponse(BaseModel):
    id: int
    name: str
    translations: Optional[Dict[str, Optional[str]]] = None
    aliases: Optional[List[TvdbAlias]] = None
    overview: Optional[str] = None
    image: Optional[str] = None
    first_aired: Optional[str] = Field(None, alias="firstAired")
    last_aired: Optional[str] = Field(None, alias="lastAired")
    year: Optional[str] = None
    remote_ids: Optional[List[TvdbRemoteId]] = Field(None, alias="remoteIds")

class TvdbExtendedDetailsResponse(BaseModel):
    data: TvdbDetailsResponse

# --- API Endpoints ---
@router.get("/search", response_model=List[TvdbApiSearchResult], summary="搜索 TVDB 作品")
async def search_tvdb(
    keyword: str = Query(..., min_length=1),
    client: httpx.AsyncClient = Depends(get_tvdb_client),
):
    """通过关键词在 TheTVDB 上搜索影视作品。"""
    try:
        # 移除 type=series 参数以搜索所有类型（电影、电视剧等）
        response = await client.get("/search", params={"query": keyword})
        response.raise_for_status()
        results = TvdbSearchResponse.model_validate(response.json()).data

        formatted_results = []
        for item in results:
            # 构建更丰富的详情字符串
            details_parts = []
            type_map = {"series": "电视剧", "movie": "电影", "person": "人物", "company": "公司"}
            details_parts.append(f"类型: {type_map.get(item.type, item.type)}")
            if item.year:
                details_parts.append(f"年份: {item.year}")
            
            details = " / ".join(details_parts)
            
            if item.overview:
                details += f" / {item.overview[:100]}..."

            formatted_results.append(
                TvdbApiSearchResult(
                    id=item.tvdb_id,
                    title=item.name,
                    details=details,
                    imageUrl=item.image_url,
                )
            )
        return formatted_results
    except Exception as e:
        logger.error(f"TVDB 搜索失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="TVDB 搜索失败。")
async def get_tvdb_details_logic(tvdb_id: str, client: httpx.AsyncClient) -> "models.MetadataDetailsResponse":
    """获取指定 TVDB ID 的作品详情，主要用于提取别名和IMDb ID。"""
    try:
        response = await client.get(f"/series/{tvdb_id}/extended")
        response.raise_for_status()
        details = TvdbExtendedDetailsResponse.model_validate(response.json()).data

        imdb_id = None
        tmdb_id = None
        if details.remote_ids:
            for remote_id in details.remote_ids:
                if remote_id.sourceName == "IMDB":
                    imdb_id = remote_id.id
                elif remote_id.sourceName == "TheMovieDB.com":
                    tmdb_id = remote_id.id

        # 初始化所有名称字段
        name_cn = None
        name_en = None
        name_jp = None
        name_romaji = None
        other_cn_aliases = []

        # 1. 优先从 translations 字典中提取各种语言的名称
        if details.translations:
            translations = details.translations
            name_cn = translations.get("zho")
            name_en = translations.get("eng")
            name_jp = translations.get("jpn")
            # TVDB API 通常使用 'rom' 作为罗马音的键
            name_romaji = translations.get("rom")
        
        # 2. 遍历 aliases 列表，作为备用方案并收集其他中文别名
        if details.aliases:
            for alias in details.aliases:
                lang, name = alias.language, alias.name
                if lang == 'zh':
                    # 如果还没有中文名，则使用找到的第一个作为主中文名
                    if not name_cn:
                        name_cn = name
                    # 否则，如果它与主中文名不同，则加入别名列表
                    elif name != name_cn:
                        other_cn_aliases.append(name)
                elif lang == 'en' and not name_en:
                    name_en = name
                elif lang == 'ja' and not name_jp:
                    name_jp = name

        # 3. 如果在 translations 和 aliases 中都找不到中文名，则使用记录的主名称作为最后的备用方案
        if not name_cn:
            name_cn = details.name
        
        return models.MetadataDetailsResponse(
            id=str(details.id),
            tvdbId=str(details.id),
            title=name_cn,
            nameEn=name_en,
            nameJp=name_jp,
            nameRomaji=name_romaji,
            aliasesCn=list(dict.fromkeys(other_cn_aliases)),
            imdbId=imdb_id,
            tmdbId=tmdb_id
        )
    except Exception as e:
        logger.error(f"获取 TVDB 详情失败 (ID: {tvdb_id}): {e}", exc_info=True)
        raise ValueError(f"获取 TVDB 详情失败 (ID: {tvdb_id})。")

@router.get("/details/{tvdb_id}", response_model=models.MetadataDetailsResponse, summary="获取 TVDB 作品详情(扩展)")
async def get_tvdb_details(
    tvdb_id: str = Path(...), client: httpx.AsyncClient = Depends(get_tvdb_client)
):
    """获取指定 TVDB ID 的作品详情，主要用于提取别名和IMDb ID。"""
    try:
        return await get_tvdb_details_logic(tvdb_id, client)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

async def search_tvdb_aliases(keyword: str, client: httpx.AsyncClient) -> Set[str]:
    """从 TVDB 获取别名。"""
    local_aliases: Set[str] = set()
    try:
        # Re-implementing the logic from the search_tvdb endpoint
        search_response = await client.get("/search", params={"query": keyword})
        search_response.raise_for_status()
        search_results = TvdbSearchResponse.model_validate(search_response.json()).data

        if not search_results:
            return local_aliases

        best_match_id = search_results[0].tvdb_id

        # Re-implementing the logic from get_tvdb_details endpoint
        details_response = await client.get(f"/series/{best_match_id}/extended")
        details_response.raise_for_status() # type: ignore
        details = TvdbExtendedDetailsResponse.model_validate(details_response.json()).data

        # The main title from TVDB is often in English
        if details.name:
            local_aliases.add(details.name)

        # Add Chinese name from translations if available
        if details.translations and details.translations.get("zho"):
            local_aliases.add(details.translations["zho"])

        logger.info(f"TVDB辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
    except Exception as e:
        logger.warning(f"TVDB辅助搜索失败: {e}")

    return {alias for alias in local_aliases if alias}
