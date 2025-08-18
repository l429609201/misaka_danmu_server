import asyncio
import logging
import re

import aiomysql
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status, Path
from pydantic import BaseModel, Field, ValidationError
from typing import List, Dict, Optional, Any

from .. import crud, models, security
from ..config import settings
from ..database import get_db_pool

logger = logging.getLogger(__name__)
router = APIRouter()

def _is_cjk(s: str) -> bool:
    """检查字符串是否包含中日韩字符。"""
    if not s:
        return False
    # CJK Unified Ideographs, Hiragana, Katakana
    return any('\u4e00' <= char <= '\u9fff' or '\u3040' <= char <= '\u309f' or '\u30a0' <= char <= '\u30ff' for char in s)


def _clean_movie_title(title: Optional[str]) -> Optional[str]:
    """
    从标题中移除 "剧场版" 或 "The Movie" 等词语。
    """
    if not title:
        return None
    # A list of phrases to remove, case-insensitively
    phrases_to_remove = ["劇場版", "the movie"]
    
    cleaned_title = title
    for phrase in phrases_to_remove:
        # This regex removes the phrase, optional surrounding whitespace, and an optional trailing colon.
        cleaned_title = re.sub(r'\s*' + re.escape(phrase) + r'\s*:?', '', cleaned_title, flags=re.IGNORECASE)

    # Clean up any double spaces that might result from the removal and leading/trailing separators
    cleaned_title = re.sub(r'\s{2,}', ' ', cleaned_title).strip().strip(':- ')
    
    return cleaned_title


async def _get_robust_image_base_url(pool: aiomysql.Pool) -> str:
    """
    获取TMDB图片基础URL，并对其进行健壮性处理。
    如果用户只配置了域名，则自动附加默认的尺寸路径。
    """
    image_base_url_config = await crud.get_config_value(
        pool, "tmdb_image_base_url", "https://image.tmdb.org/t/p/w500"
    )
    
    # 如果配置中不包含 /t/p/ 路径，说明用户可能只填写了域名
    if '/t/p/' not in image_base_url_config:
        # 我们附加一个默认的尺寸路径，使其成为一个有效的图片基础URL
        return f"{image_base_url_config.rstrip('/')}/t/p/w500"
    
    return image_base_url_config

async def get_tmdb_client(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
) -> httpx.AsyncClient:
    """依赖项：创建一个带有 TMDB 授权的 httpx 客户端。"""
    # Proxy logic
    proxy_url_task = crud.get_config_value(pool, "proxy_url", "")
    proxy_enabled_globally_task = crud.get_config_value(pool, "proxy_enabled", "false")
    metadata_settings_task = crud.get_all_metadata_source_settings(pool)

    proxy_url, proxy_enabled_str, metadata_settings = await asyncio.gather(
        proxy_url_task, proxy_enabled_globally_task, metadata_settings_task
    )
    proxy_enabled_globally = proxy_enabled_str.lower() == 'true'

    tmdb_setting = next((s for s in metadata_settings if s['provider_name'] == 'tmdb'), None)
    use_proxy_for_tmdb = tmdb_setting.get('use_proxy', False) if tmdb_setting else False

    proxies = proxy_url if proxy_enabled_globally and use_proxy_for_tmdb and proxy_url else None

    # Fetch all configs in parallel
    keys = ["tmdb_api_key", "tmdb_api_base_url"]
    tasks = [crud.get_config_value(pool, key, "") for key in keys]
    api_key, domain = await asyncio.gather(*tasks)

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TMDB API Key not configured. Please set it in the settings page."
        )
    if not domain:
        domain = "https://api.themoviedb.org" # Fallback to default domain
        logger.warning("TMDB API 域名未配置，将使用默认域名。")

    # 从域名构建完整的 API 基础 URL
    # 增加健壮性：如果用户输入的域名已经包含了 /3，则不再重复添加
    cleaned_domain = domain.rstrip('/')
    if cleaned_domain.endswith('/3'):
        base_url = cleaned_domain
    else:
        base_url = f"{cleaned_domain}/3"

    # TMDB v3 API 使用 api_key 查询参数进行身份验证
    params = {"api_key": api_key}
    headers = {
        # 使用一个更通用的 User-Agent
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    return httpx.AsyncClient(base_url=base_url, params=params, headers=headers, timeout=20.0, proxy=proxies)

# --- Pydantic Models for TMDB API ---
# Most models have been moved to src/models.py.
# Models still used locally are kept here.

class TMDBTVResult(BaseModel):
    id: int
    name: str
    poster_path: Optional[str] = None

class TMDBMovieResult(BaseModel):
    id: int
    title: str
    poster_path: Optional[str] = None

class TMDBTVSearchResults(BaseModel):
    results: List[TMDBTVResult]
    total_pages: int

class TMDBMovieSearchResults(BaseModel):
    results: List[TMDBMovieResult]
    total_pages: int

# This model is required by get_tmdb_episode_groups but was missed during refactoring.
# It is kept here to ensure the file is runnable.
class TMDBEpisodeGroup(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    episode_count: int
    group_count: int
    type: int

class TMDBSearchResponseItem(BaseModel):
    id: int
    name: str
    image_url: Optional[str] = None

class TMDBExternalIDs(BaseModel):
    imdb_id: Optional[str] = None
    tvdb_id: Optional[int] = None

class TMDBAlternativeTitle(BaseModel):
    iso_3166_1: str
    title: str
    type: str

class TMDBAlternativeTitles(BaseModel):
    titles: List[TMDBAlternativeTitle] = []

class TMDBTVDetails(BaseModel):
    id: int
    name: str
    original_language: str
    original_name: str
    alternative_titles: Optional[TMDBAlternativeTitles] = None
    external_ids: Optional[TMDBExternalIDs] = None

class TMDBMovieDetails(BaseModel):
    id: int
    original_language: str
    title: str
    original_title: str
    alternative_titles: Optional[TMDBAlternativeTitles] = None
    external_ids: Optional[TMDBExternalIDs] = None

class TMDBEpisodeWithStill(BaseModel):
    id: int
    still_path: Optional[str] = None

class TMDBSeasonDetails(BaseModel):
    episodes: List[TMDBEpisodeWithStill]

@router.get("/search/tv", response_model=List[TMDBSearchResponseItem], summary="搜索 TMDB 电视剧")
async def search_tmdb_subjects(
    keyword: str = Query(..., min_length=1),
    client: httpx.AsyncClient = Depends(get_tmdb_client),
    pool: aiomysql.Pool = Depends(get_db_pool),
):
    cache_key = f"tmdb_search_tv_{keyword}"
    cached_results = await crud.get_cache(pool, cache_key)
    if cached_results is not None:
        logger.info(f"TMDB 电视剧搜索 '{keyword}' 命中缓存。")
        # Pydantic will re-validate the cached data against the response_model
        return cached_results

    async with client:
        # 步骤 1: 初始搜索以获取ID列表
        params = {
            "query": keyword,
            "include_adult": False,
            "language": "zh-CN",
        }
        search_response = await client.get(
            "/search/tv", params=params
        )

        if search_response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TMDB API Key 无效或未授权，请在设置中检查您的密钥。",
            )

        if search_response.status_code != 200:
            logger.error(f"TMDB 电视剧搜索失败，状态码: {search_response.status_code}，响应: {search_response.text}")
            return []

        search_result = TMDBTVSearchResults.model_validate(search_response.json())
        if not search_result.results:
            return []

        # Fetch image base URL
        image_base_url = await _get_robust_image_base_url(pool)

        # 步骤 3: 组合并格式化最终结果
        final_results = []
        for subject in search_result.results:
            image_url = f"{image_base_url.rstrip('/')}{subject.poster_path}" if subject.poster_path else None
            try:
                final_results.append(
                    {
                        "id": subject.id,
                        "name": subject.name,
                        "image_url": image_url,
                    }
                )
            except ValidationError as e:
                logger.error(f"验证 TMDB subject 详情失败: {e}")

        # 缓存结果
        ttl_seconds_str = await crud.get_config_value(pool, 'metadata_search_ttl_seconds', '1800')
        await crud.set_cache(pool, cache_key, final_results, int(ttl_seconds_str), provider='tmdb')

        return final_results
 
@router.get("/search/movie", response_model=List[TMDBSearchResponseItem], summary="搜索 TMDB 电影作品")
async def search_tmdb_movie_subjects(
    keyword: str = Query(..., min_length=1),
    client: httpx.AsyncClient = Depends(get_tmdb_client),
    pool: aiomysql.Pool = Depends(get_db_pool),
):
    cache_key = f"tmdb_search_movie_{keyword}"
    cached_results = await crud.get_cache(pool, cache_key)
    if cached_results is not None:
        logger.info(f"TMDB 电影搜索 '{keyword}' 命中缓存。")
        return cached_results

    async with client:
        # 步骤 1: 初始搜索以获取ID列表
        params = {
            "query": keyword,
            "include_adult": False,
            "language": "zh-CN",
        }
        search_response = await client.get(
            "/search/movie", params=params
        )

        if search_response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TMDB API Key 无效或未授权，请在设置中检查您的密钥。",
            )

        if search_response.status_code != 200:
            logger.error(f"TMDB 电影搜索失败，状态码: {search_response.status_code}，响应: {search_response.text}")
            return []

        search_result = TMDBMovieSearchResults.model_validate(search_response.json())
        if not search_result.results:
            return []

        # Fetch image base URL
        image_base_url = await _get_robust_image_base_url(pool)

        results = [
            {
                "id": subject.id,
                "name": subject.title,
                "image_url": f"{image_base_url.rstrip('/')}{subject.poster_path}" if subject.poster_path else None
            }
            for subject in search_result.results
        ]
        # 缓存结果
        ttl_seconds_str = await crud.get_config_value(pool, 'metadata_search_ttl_seconds', '1800')
        await crud.set_cache(pool, cache_key, results, int(ttl_seconds_str), provider='tmdb')
        return results


@router.get("/details/{media_type}/{tmdb_id}", response_model=Dict[str, Any], summary="获取 TMDB 作品详情")
async def get_tmdb_details(
    media_type: str = Path(..., description="媒体类型, 'tv' 或 'movie'"),
    tmdb_id: int = Path(..., description="TMDB ID"),
    season: Optional[int] = Query(None, description="要专门为其获取别名的季度号"),
    client: httpx.AsyncClient = Depends(get_tmdb_client),
):
    if media_type not in ["tv", "movie"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的媒体类型，必须是 'tv' 或 'movie'。")

    async with client:
        # --- New parallel fetching logic ---
        details_cn_task = client.get(f"/{media_type}/{tmdb_id}", params={"append_to_response": "alternative_titles,external_ids", "language": "zh-CN"})
        details_en_task = client.get(f"/{media_type}/{tmdb_id}", params={"language": "en-US"})
        details_ja_task = client.get(f"/{media_type}/{tmdb_id}", params={"language": "ja-JP"})
        details_tw_task = client.get(f"/{media_type}/{tmdb_id}", params={"language": "zh-TW"})

        responses = await asyncio.gather(
            details_cn_task, details_en_task, details_ja_task, details_tw_task,
            return_exceptions=True
        )
        
        details_cn_res, details_en_res, details_ja_res, details_tw_res = responses

        # --- Process Chinese (main) response ---
        if isinstance(details_cn_res, Exception) or details_cn_res.status_code != 200:
            # Handle potential 401/404 from the main request
            if isinstance(details_cn_res, httpx.Response):
                if details_cn_res.status_code == 401:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TMDB API Key 无效或未授权。")
                if details_cn_res.status_code == 404:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到 TMDB 条目。")
            
            logger.error(f"获取 TMDB 中文详情失败 (ID: {tmdb_id}): {details_cn_res}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"获取 TMDB 详情失败 (ID: {tmdb_id})。")
        
        details_json = details_cn_res.json()

        # Initialize variables
        name_en = None
        name_jp = None
        name_romaji = None
        aliases_cn = []
        imdb_id = None
        tvdb_id = None
        
        # Get base details
        if media_type == "tv":
            details = TMDBTVDetails.model_validate(details_json)
            main_title_cn = details.name
        else: # movie
            details = TMDBMovieDetails.model_validate(details_json)
            main_title_cn = details.title

        # Extract external IDs
        if details.external_ids:
            imdb_id = details.external_ids.imdb_id
            tvdb_id = details.external_ids.tvdb_id

        # Process alternative titles from Chinese response
        if details.alternative_titles:
            for alt_title in details.alternative_titles.titles: # type: ignore
                title_text = alt_title.title
                alt_type = alt_title.type or ""
                alt_country = alt_title.iso_3166_1

                if not title_text: continue

                # Get Romaji from JP region with type "Romaji"
                if alt_country == "JP" and alt_type == "Romaji":
                    if not name_romaji and not _is_cjk(title_text):
                        name_romaji = title_text
                    continue

                # Handle Chinese Aliases
                if alt_country in ["CN", "HK", "TW"]:
                    season_match = re.match(r'Season\s*(\d+)', alt_type, re.IGNORECASE)
                    if season_match:
                        title_season = int(season_match.group(1))
                        if season and season == title_season and _is_cjk(title_text):
                            aliases_cn.append(title_text)
                    # Add generic aliases (no type)
                    elif not alt_type and _is_cjk(title_text):
                        aliases_cn.append(title_text)

        # --- Process other language responses ---
        if isinstance(details_en_res, httpx.Response) and details_en_res.status_code == 200:
            en_json = details_en_res.json()
            name_en = en_json.get('name') or en_json.get('title')

        if isinstance(details_ja_res, httpx.Response) and details_ja_res.status_code == 200:
            ja_json = details_ja_res.json()
            name_jp = ja_json.get('name') or ja_json.get('title')

        if isinstance(details_tw_res, httpx.Response) and details_tw_res.status_code == 200:
            tw_json = details_tw_res.json()
            name_tw = tw_json.get('name') or tw_json.get('title')
            if name_tw and _is_cjk(name_tw):
                aliases_cn.append(name_tw)
        
        if main_title_cn: aliases_cn.append(main_title_cn)
        
        cleaned_aliases_cn = [_clean_movie_title(alias) for alias in aliases_cn if alias]

        return {
            "id": details.id,
            "imdb_id": imdb_id,
            "tvdb_id": tvdb_id,
            "name_en": _clean_movie_title(name_en),
            "name_jp": _clean_movie_title(name_jp),
            "name_romaji": _clean_movie_title(name_romaji),
            "aliases_cn": list(dict.fromkeys(cleaned_aliases_cn)) # Deduplicate
        }

@router.get("/tv/{tmdb_id}/episode_groups", response_model=List[TMDBEpisodeGroup], summary="获取电视剧的所有剧集组")
async def get_tmdb_episode_groups(
    tmdb_id: int = Path(..., description="TMDB电视剧ID"),
    client: httpx.AsyncClient = Depends(get_tmdb_client),
    pool: aiomysql.Pool = Depends(get_db_pool),
):
    cache_key = f"tmdb_episode_groups_{tmdb_id}"
    cached_results = await crud.get_cache(pool, cache_key)
    if cached_results is not None:
        logger.info(f"TMDB 电视剧(ID: {tmdb_id})的剧集组列表命中缓存。")
        return cached_results

    async with client:
        response = await client.get(f"/tv/{tmdb_id}/episode_groups")
        if response.status_code == 401:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TMDB API Key 无效。")
        if response.status_code == 404:
            return [] # Not found is a valid case, just return empty list
        response.raise_for_status()

        data = response.json()
        results = [TMDBEpisodeGroup.model_validate(r) for r in data.get("results", [])]
        results_to_cache = [r.model_dump() for r in results]
        ttl_seconds_str = await crud.get_config_value(pool, 'metadata_search_ttl_seconds', '1800')
        await crud.set_cache(pool, cache_key, results_to_cache, int(ttl_seconds_str), provider='tmdb')
        return results

@router.get("/episode_group/{group_id}", response_model=models.EnrichedTMDBEpisodeGroupDetails, summary="获取特定剧集组的详情")
async def get_tmdb_episode_group_details(
    group_id: str = Path(..., description="TMDB剧集组ID"),
    tv_id: int = Query(..., description="TMDB电视剧ID, 用于获取图片"),
    client: httpx.AsyncClient = Depends(get_tmdb_client),
    pool: aiomysql.Pool = Depends(get_db_pool),
):
    cache_key = f"tmdb_episode_group_details_{group_id}_{tv_id}"
    cached_results = await crud.get_cache(pool, cache_key)
    if cached_results is not None:
        logger.info(f"TMDB 剧集组详情(ID: {group_id})命中缓存。")
        return cached_results

    async with client:
        # Step 1: Fetch details in parallel for Chinese and Japanese
        zh_task = client.get(f"/tv/episode_group/{group_id}", params={"language": "zh-CN"})
        ja_task = client.get(f"/tv/episode_group/{group_id}", params={"language": "ja-JP"})
        zh_response, ja_response = await asyncio.gather(zh_task, ja_task, return_exceptions=True)

        # Handle primary (Chinese) response errors
        if isinstance(zh_response, Exception):
            logger.error(f"获取剧集组 {group_id} 的中文详情失败: {zh_response}")
            raise HTTPException(status_code=500, detail="获取剧集组详情失败。")
        if zh_response.status_code == 401:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TMDB API Key 无效。")
        if zh_response.status_code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到剧集组。")
        zh_response.raise_for_status()
        
        # Use the Chinese response as the base
        details = models.TMDBEpisodeGroupDetails.model_validate(zh_response.json())
        # 按照 order 字段对剧集组进行排序
        details.groups.sort(key=lambda g: g.order)

        # Step 2: Build Japanese name map
        ja_name_map = {}
        if isinstance(ja_response, httpx.Response) and ja_response.status_code == 200:
            try:
                ja_details = models.TMDBEpisodeGroupDetails.model_validate(ja_response.json())
                for group in ja_details.groups:
                    for episode in group.episodes:
                        ja_name_map[episode.id] = episode.name
            except ValidationError as e:
                logger.warning(f"解析剧集组 {group_id} 的日文详情失败: {e}")

        # Step 3: Fetch image paths by fetching season details
        image_map = {}
        season_numbers = {ep.season_number for group in details.groups for ep in group.episodes}
        
        async def fetch_season_stills(season_num: int):
            try:
                res = await client.get(f"/tv/{tv_id}/season/{season_num}")
                res.raise_for_status()
                season_details = TMDBSeasonDetails.model_validate(res.json())
                return {ep.id: ep.still_path for ep in season_details.episodes if ep.still_path}
            except Exception as e:
                logger.warning(f"获取电视剧 {tv_id} 第 {season_num} 季的剧照失败: {e}")
                return {}

        if season_numbers:
            season_tasks = [fetch_season_stills(s_num) for s_num in season_numbers]
            season_results = await asyncio.gather(*season_tasks)
            for season_map in season_results:
                image_map.update(season_map)

        # Step 4: Construct final enriched response
        image_base_url = await _get_robust_image_base_url(pool)
        enriched_groups = []
        for group in details.groups:
            enriched_episodes = []
            for episode in group.episodes:
                still_path = image_map.get(episode.id)
                enriched_episodes.append(models.EnrichedTMDBEpisodeInGroupDetail(
                    id=episode.id, name=episode.name, episode_number=episode.episode_number,
                    season_number=episode.season_number, air_date=episode.air_date,
                    overview=episode.overview, order=episode.order,
                    name_jp=ja_name_map.get(episode.id),
                    image_url=f"{image_base_url.rstrip('/')}{still_path}" if still_path else None
                ))
            enriched_groups.append(models.EnrichedTMDBGroupInGroupDetail(
                id=group.id, name=group.name, order=group.order, episodes=enriched_episodes
            ))

        final_response_object = models.EnrichedTMDBEpisodeGroupDetails(**details.model_dump(exclude={'groups'}), groups=enriched_groups)
        
        # Cache the final object
        ttl_seconds_str = await crud.get_config_value(pool, 'metadata_search_ttl_seconds', '1800')
        await crud.set_cache(pool, cache_key, final_response_object.model_dump(), int(ttl_seconds_str), provider='tmdb')

        return final_response_object
