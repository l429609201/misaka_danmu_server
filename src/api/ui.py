import re
from typing import Optional, List, Any, Dict, Callable
import asyncio
import secrets
import string
import time
from urllib.parse import urlparse, urlunparse, quote, unquote
import logging

from datetime import timedelta, datetime, timezone
import aiomysql
import httpx
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status, Response
from fastapi.security import OAuth2PasswordRequestForm

from .. import crud, models, security, scraper_manager
from ..log_manager import get_logs
from ..task_manager import TaskManager, TaskSuccess, TaskStatus
from ..metadata_manager import MetadataSourceManager
from ..scraper_manager import ScraperManager
from ..webhook_manager import WebhookManager
from ..image_utils import download_image
from ..scheduler import SchedulerManager
from thefuzz import fuzz
from .tmdb_api import get_tmdb_client, _get_robust_image_base_url
from .douban_api import get_douban_client
from .imdb_api import get_imdb_client
from ..config import settings
from ..database import get_db_pool

router = APIRouter()
auth_router = APIRouter()
logger = logging.getLogger(__name__)

def _roman_to_int(s: str) -> int:
    """将罗马数字字符串转换为整数。"""
    roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    s = s.upper()
    result = 0
    i = 0
    while i < len(s):
        # 处理减法规则 (e.g., IV, IX)
        if i + 1 < len(s) and roman_map[s[i]] < roman_map[s[i+1]]:
            result += roman_map[s[i+1]] - roman_map[s[i]]
            i += 2
        else:
            result += roman_map[s[i]]
            i += 1
    return result

def parse_search_keyword(keyword: str) -> Dict[str, Any]:
    """
    解析搜索关键词，提取标题、季数和集数。
    支持 "Title S01E01", "Title S01", "Title 2", "Title 第二季", "Title Ⅲ" 等格式。
    """
    keyword = keyword.strip()

    # 1. 优先匹配 SXXEXX 格式
    s_e_pattern = re.compile(r"^(?P<title>.+?)\s*S(?P<season>\d{1,2})E(?P<episode>\d{1,4})$", re.IGNORECASE)
    match = s_e_pattern.match(keyword)
    if match:
        data = match.groupdict()
        return {
            "title": data["title"].strip(),
            "season": int(data["season"]),
            "episode": int(data["episode"]),
        }

    # 2. 匹配季度信息
    season_patterns = [
        (re.compile(r"^(.*?)\s*(?:S|Season)\s*(\d{1,2})$", re.I), lambda m: int(m.group(2))),
        (re.compile(r"^(.*?)\s*第\s*([一二三四五六七八九十\d]+)\s*[季部]$", re.I), 
         lambda m: {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}.get(m.group(2)) or int(m.group(2))),
        (re.compile(r"^(.*?)\s*([Ⅰ-Ⅻ])$"), 
         lambda m: {'Ⅰ': 1, 'Ⅱ': 2, 'Ⅲ': 3, 'Ⅳ': 4, 'Ⅴ': 5, 'Ⅵ': 6, 'Ⅶ': 7, 'Ⅷ': 8, 'Ⅸ': 9, 'Ⅹ': 10, 'Ⅺ': 11, 'Ⅻ': 12}.get(m.group(2).upper())),
        (re.compile(r"^(.*?)\s+([IVXLCDM]+)$", re.I), lambda m: _roman_to_int(m.group(2))),
        (re.compile(r"^(.*?)\s+(\d{1,2})$"), lambda m: int(m.group(2))),
    ]

    for pattern, handler in season_patterns:
        match = pattern.match(keyword)
        if match:
            try:
                title = match.group(1).strip()
                season = handler(match)
                if season and not (len(title) > 4 and title[-4:].isdigit()): # 避免将年份误认为季度
                    return {"title": title, "season": season, "episode": None}
            except (ValueError, KeyError, IndexError):
                continue

    # 3. 如果没有匹配到特定格式，则返回原始标题
    return {"title": keyword, "season": None, "episode": None}

async def get_scraper_manager(request: Request) -> ScraperManager:
    """依赖项：从应用状态获取 Scraper 管理器"""
    return request.app.state.scraper_manager

async def get_task_manager(request: Request) -> TaskManager:
    """依赖项：从应用状态获取任务管理器"""
    return request.app.state.task_manager

async def get_scheduler_manager(request: Request) -> SchedulerManager:
    """依赖项：从应用状态获取 Scheduler 管理器"""
    return request.app.state.scheduler_manager

async def get_webhook_manager(request: Request) -> WebhookManager:
    """依赖项：从应用状态获取 Webhook 管理器"""
    return request.app.state.webhook_manager

async def get_metadata_manager(request: Request) -> MetadataSourceManager:
    """依赖项：从应用状态获取元数据源管理器"""
    return request.app.state.metadata_manager


async def update_tmdb_mappings(
    pool: aiomysql.Pool,
    client: httpx.AsyncClient,
    tmdb_tv_id: int,
    group_id: str
):
    """
    获取TMDB剧集组详情并将其映射关系存入数据库。
    """
    async with client:
        response = await client.get(f"/tv/episode_group/{group_id}", params={"language": "zh-CN"})
        response.raise_for_status()
        
        group_details = models.TMDBEpisodeGroupDetails.model_validate(response.json())
        
        await crud.save_tmdb_episode_group_mappings(
            pool=pool,
            tmdb_tv_id=tmdb_tv_id,
            group_id=group_id,
            group_details=group_details
        )

def _get_season_from_title(title: str) -> int:
    """从标题中解析季度信息，返回季度数。"""
    if not title:
        return 1
    
    # 模式的顺序很重要
    patterns = [
        (re.compile(r"(?:S|Season)\s*(\d+)", re.I), lambda m: int(m.group(1))),
        (re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*[季部]", re.I), 
         lambda m: {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}.get(m.group(1)) or int(m.group(1))),
        (re.compile(r"\s+([Ⅰ-Ⅻ])\b", re.I), 
         lambda m: {'Ⅰ': 1, 'Ⅱ': 2, 'Ⅲ': 3, 'Ⅳ': 4, 'Ⅴ': 5, 'Ⅵ': 6, 'Ⅶ': 7, 'Ⅷ': 8, 'Ⅸ': 9, 'Ⅹ': 10, 'Ⅺ': 11, 'Ⅻ': 12}.get(m.group(1).upper())),
        (re.compile(r"\s+([IVXLCDM]+)$", re.I), lambda m: _roman_to_int(m.group(1))),
    ]

    for pattern, handler in patterns:
        match = pattern.search(title)
        if match:
            try:
                if season := handler(match): return season
            except (ValueError, KeyError, IndexError):
                continue
    return 1 # Default to season 1

@router.get(
    "/search/anime",
    response_model=models.AnimeSearchResponse,
    summary="搜索本地数据库中的节目信息",
)
async def search_anime_local(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    pool=Depends(get_db_pool)
):
    db_results = await crud.search_anime(pool, keyword)
    animes = [
        models.AnimeInfo(animeId=item["id"], animeTitle=item["title"], type=item["type"])
        for item in db_results
    ]
    return models.AnimeSearchResponse(animes=animes)

class UIProviderSearchResponse(models.ProviderSearchResponse):
    """扩展了 ProviderSearchResponse 以包含原始搜索的上下文。"""
    search_season: Optional[int] = None
    search_episode: Optional[int] = None
@router.get(
    "/search/provider",
    response_model=UIProviderSearchResponse,
    summary="从外部数据源搜索节目",
)
async def search_anime_provider(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    manager: ScraperManager = Depends(get_scraper_manager),
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """
    从所有已配置的数据源（如腾讯、B站等）搜索节目信息。
    支持 "标题 SXXEXX" 格式来指定集数。
    如果配置了TMDB API Key，会先用关键词搜索TMDB获取别名，然后用原始关键词搜索所有弹幕源，最后用获取到的别名集对搜索结果进行过滤。
    如果未配置，则直接使用关键词进行搜索。
    """
    parsed_keyword = parse_search_keyword(keyword)
    search_title = parsed_keyword["title"]
    season_to_filter = parsed_keyword["season"]
    episode_to_filter = parsed_keyword["episode"]

    episode_info = {
        "season": season_to_filter,
        "episode": episode_to_filter
    } if episode_to_filter is not None else None

    logger.info(f"用户 '{current_user.username}' 正在搜索: '{keyword}' (解析为: title='{search_title}', season={season_to_filter}, episode={episode_to_filter})")
    if not manager.has_enabled_scrapers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="没有启用的弹幕搜索源，请在“搜索源”页面中启用至少一个。"
        )

    tmdb_api_key = await crud.get_config_value(pool, "tmdb_api_key", "")

    if not tmdb_api_key:
        logger.info("TMDB API Key 未配置，跳过辅助搜索，直接进行全网搜索。")
        results = await manager.search_all([search_title], episode_info=episode_info)
        logger.info(f"直接搜索完成，找到 {len(results)} 个原始结果。")
    else:
        logger.info("TMDB API Key 已配置，将执行元数据辅助搜索。")
        tmdb_domain = await crud.get_config_value(pool, "tmdb_api_base_url", "https://api.themoviedb.org")
        cleaned_domain = tmdb_domain.rstrip('/')
        base_url = cleaned_domain if cleaned_domain.endswith('/3') else f"{cleaned_domain}/3"
        params = {"api_key": tmdb_api_key}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        
        async with httpx.AsyncClient(base_url=base_url, params=params, headers=headers, timeout=20.0) as tmdb_client:
            filter_aliases = {search_title}

            async def _get_tmdb_aliases() -> set:
                """从TMDB获取别名。"""
                local_aliases = set()
                try:
                    tv_task = tmdb_client.get("/search/tv", params={"query": search_title, "language": "zh-CN"})
                    movie_task = tmdb_client.get("/search/movie", params={"query": search_title, "language": "zh-CN"})
                    tv_res, movie_res = await asyncio.gather(tv_task, movie_task, return_exceptions=True)

                    tmdb_results = []
                    if isinstance(tv_res, httpx.Response) and tv_res.status_code == 200:
                        tmdb_results.extend(tv_res.json().get("results", []))
                    if isinstance(movie_res, httpx.Response) and movie_res.status_code == 200:
                        tmdb_results.extend(movie_res.json().get("results", []))

                    if tmdb_results:
                        best_match = tmdb_results[0]
                        media_type = "tv" if "name" in best_match else "movie"
                        media_id = best_match['id']

                        details_cn_task = tmdb_client.get(f"/{media_type}/{media_id}", params={"append_to_response": "alternative_titles", "language": "zh-CN"})
                        details_tw_task = tmdb_client.get(f"/{media_type}/{media_id}", params={"language": "zh-TW"})
                        details_cn_res, details_tw_res = await asyncio.gather(details_cn_task, details_tw_task, return_exceptions=True)

                        if isinstance(details_cn_res, httpx.Response) and details_cn_res.status_code == 200:
                            details = details_cn_res.json()
                            local_aliases.add(details.get('name') or details.get('title'))
                            local_aliases.add(details.get('original_name') or details.get('original_title'))
                            alt_titles = details.get("alternative_titles", {}).get("titles", [])
                            for title_info in alt_titles:
                                local_aliases.add(title_info['title'])
                        
                        if isinstance(details_tw_res, httpx.Response) and details_tw_res.status_code == 200:
                            details_tw = details_tw_res.json()
                            local_aliases.add(details_tw.get('name') or details_tw.get('title'))

                        logger.info(f"TMDB辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
                except Exception as e:
                    logger.warning(f"TMDB辅助搜索失败: {e}")
                return {alias for alias in local_aliases if alias}

            tasks = [_get_tmdb_aliases()]
            results_from_helpers = await asyncio.gather(*tasks)
            for alias_set in results_from_helpers:
                filter_aliases.update(alias_set)
            logger.info(f"所有辅助搜索完成，最终别名集大小: {len(filter_aliases)}")

            logger.info(f"将使用解析后的标题 '{search_title}' 进行全网搜索...")
            all_results = await manager.search_all([search_title], episode_info=episode_info)

            def normalize_for_filtering(title: str) -> str:
                if not title: return ""
                title = re.sub(r'[\[【(（].*?[\]】)）]', '', title)
                return title.lower().replace(" ", "").replace("：", ":").strip()
            normalized_filter_aliases = {normalize_for_filtering(alias) for alias in filter_aliases if alias}
            filtered_results = []
            for item in all_results:
                normalized_item_title = normalize_for_filtering(item.title)
                if not normalized_item_title: continue
                if any((alias in normalized_item_title) or (normalized_item_title in alias) for alias in normalized_filter_aliases):
                    filtered_results.append(item)
            logger.info(f"别名过滤: 从 {len(all_results)} 个原始结果中，保留了 {len(filtered_results)} 个相关结果。")
            results = filtered_results

    # 辅助函数，用于根据标题修正媒体类型
    def is_movie_by_title(title: str) -> bool:
        if not title:
            return False
        # 关键词列表，不区分大小写
        movie_keywords = ["剧场版", "劇場版", "movie", "映画"]
        title_lower = title.lower()
        return any(keyword in title_lower for keyword in movie_keywords)

    # 新增逻辑：根据标题关键词修正媒体类型
    for item in results:
        if item.type == 'tv_series' and is_movie_by_title(item.title):
            logger.info(f"标题 '{item.title}' 包含电影关键词，类型从 'tv_series' 修正为 'movie'。")
            item.type = 'movie'

    # 如果用户在搜索词中明确指定了季度，则对结果进行过滤
    if season_to_filter:
        original_count = len(results)
        # 当指定季度时，我们只关心电视剧类型
        filtered_by_type = [item for item in results if item.type == 'tv_series']
        
        # 然后在电视剧类型中，我们按季度号过滤
        filtered_by_season = []
        for item in filtered_by_type:
            # 使用模型中已解析好的 season 字段进行比较
            if item.season == season_to_filter:
                filtered_by_season.append(item)
        
        logger.info(f"根据指定的季度 ({season_to_filter}) 进行过滤，从 {original_count} 个结果中保留了 {len(filtered_by_season)} 个。")
        results = filtered_by_season

    # 修正：在返回结果前，确保 currentEpisodeIndex 与本次请求的 episode_info 一致。
    # 这可以防止因缓存或其他原因导致的状态泄露。
    current_episode_index_for_this_request = episode_info.get("episode") if episode_info else None
    for item in results:
        item.currentEpisodeIndex = current_episode_index_for_this_request

    # 新增：根据搜索源的显示顺序和标题相似度对结果进行排序
    source_settings = await crud.get_all_scraper_settings(pool)
    source_order_map = {s['provider_name']: s['display_order'] for s in source_settings}

    def sort_key(item: models.ProviderSearchInfo):
        provider_order = source_order_map.get(item.provider, 999)
        # 使用 token_set_ratio 来获得更鲁棒的标题相似度评分
        similarity_score = fuzz.token_set_ratio(search_title, item.title)
        # 主排序键：源顺序（升序）；次排序键：相似度（降序）
        return (provider_order, -similarity_score)

    sorted_results = sorted(results, key=sort_key)

    return UIProviderSearchResponse(
        results=sorted_results,
        search_season=season_to_filter,
        search_episode=episode_to_filter
    )

@router.get("/library/episodes-by-title", response_model=List[int], summary="根据作品标题获取已存在的分集序号")
async def get_existing_episode_indices(
    title: str = Query(..., description="要查询的作品标题"),
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """
    根据一个作品的标题，查询弹幕库中该作品已存在的所有分集的序号列表。
    用于在“编辑导入”界面实现增量导入。
    """
    return await crud.get_episode_indices_by_anime_title(pool, title)



@router.get("/search/episodes", response_model=List[models.ProviderEpisodeInfo], summary="获取搜索结果的分集列表")
async def get_episodes_for_search_result(
    provider: str = Query(...),
    media_id: str = Query(...),
    media_type: Optional[str] = Query(None), # Pass media_type to help scraper
    manager: ScraperManager = Depends(get_scraper_manager),
    current_user: models.User = Depends(security.get_current_user)
):
    """为指定的搜索结果获取完整的分集列表。"""
    try:
        scraper = manager.get_scraper(provider)
        # 将 db_media_type 传递给 get_episodes 以帮助需要它的刮削器（如 mgtv）
        episodes = await scraper.get_episodes(media_id, db_media_type=media_type)
        return episodes
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"获取分集列表失败 (provider={provider}, media_id={media_id}): {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取分集列表失败。")


@router.get("/library", response_model=models.LibraryResponse, summary="获取媒体库内容")
async def get_library(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取数据库中所有已收录的番剧信息，用于“弹幕情况”展示。"""
    db_results = await crud.get_library_anime(pool)
    # Pydantic 会自动处理 datetime 到 ISO 8601 字符串的转换
    animes = [models.LibraryAnimeInfo.model_validate(item) for item in db_results]
    return models.LibraryResponse(animes=animes)

@router.get("/library/anime/{anime_id}/details", response_model=models.AnimeFullDetails, summary="获取影视完整详情")
async def get_anime_full_details(
    anime_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取指定番剧的完整信息，包括所有元数据ID。"""
    details = await crud.get_anime_full_details(pool, anime_id)
    if not details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anime not found")
    return models.AnimeFullDetails.model_validate(details)

@router.put("/library/anime/{anime_id}", status_code=status.HTTP_204_NO_CONTENT, summary="编辑影视信息")
async def edit_anime_info(
    anime_id: int,
    update_data: models.AnimeDetailUpdate,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    client: httpx.AsyncClient = Depends(get_tmdb_client)
):
    """更新指定番剧的标题、季度和元数据。"""
    updated = await crud.update_anime_details(pool, anime_id, update_data)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品未找到或更新失败")
    logger.info(f"用户 '{current_user.username}' 更新了番剧 ID: {anime_id} 的详细信息。")

    # 新增：如果提供了TMDB ID和剧集组ID，则更新映射表
    if update_data.tmdb_id and update_data.tmdb_episode_group_id:
        logger.info(f"检测到TMDB ID和剧集组ID，开始更新映射表...")
        try:
            await update_tmdb_mappings(
                pool=pool,
                client=client,
                tmdb_tv_id=int(update_data.tmdb_id),
                group_id=update_data.tmdb_episode_group_id
            )
            logger.info(f"成功更新了 TV ID {update_data.tmdb_id} 和 Group ID {update_data.tmdb_episode_group_id} 的TMDB映射。")
        except Exception as e:
            # 仅记录错误，不中断主流程，因为核心信息已保存
            logger.error(f"更新TMDB映射失败: {e}", exc_info=True)
    return

class RefreshPosterRequest(models.BaseModel):
    image_url: str

@router.post("/library/anime/{anime_id}/refresh-poster", summary="刷新并缓存影视海报")
async def refresh_anime_poster(
    anime_id: int,
    request_data: RefreshPosterRequest,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """根据提供的URL，重新下载并缓存海报，更新数据库记录。"""
    new_local_path = await download_image(request_data.image_url, pool)
    if not new_local_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片下载失败，请检查URL或服务器日志。")

    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 同时更新原始URL和本地路径
            affected_rows = await cursor.execute(
                "UPDATE anime SET image_url = %s, local_image_path = %s WHERE id = %s",
                (request_data.image_url, new_local_path, anime_id)
            )
    if affected_rows == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品未找到。")
    return {"new_path": new_local_path}

@router.get("/library/source/{source_id}/details", summary="获取单个数据源的详情")
async def get_source_details(
    source_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取指定数据源的详细信息，包括其提供方名称。"""
    source_info = await crud.get_anime_source_info(pool, source_id)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return source_info

class ReassociationRequest(models.BaseModel):
    target_anime_id: int

@router.post("/library/anime/{source_anime_id}/reassociate", status_code=status.HTTP_204_NO_CONTENT, summary="重新关联作品的数据源")
async def reassociate_anime_sources(
    source_anime_id: int,
    request_data: ReassociationRequest,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """将一个作品的所有数据源移动到另一个作品，并删除原作品。"""
    if source_anime_id == request_data.target_anime_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="源作品和目标作品不能相同。")

    success = await crud.reassociate_anime_sources(pool, source_anime_id, request_data.target_anime_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源作品或目标作品未找到，或操作失败。")
    logger.info(f"用户 '{current_user.username}' 将作品 ID {source_anime_id} 的源关联到了 ID {request_data.target_anime_id}。")
    return

@router.delete("/library/source/{source_id}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除指定数据源的任务")
async def delete_source_from_anime(
    source_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来删除一个数据源及其所有关联的分集和弹幕。"""
    source_info = await crud.get_anime_source_info(pool, source_id)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    task_title = f"删除源: {source_info['title']} ({source_info['provider_name']})"
    task_coro = lambda callback: delete_source_task(source_id, pool, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了删除源 ID: {source_id} 的任务 (Task ID: {task_id})。")
    return {"message": f"删除源 '{source_info['provider_name']}' 的任务已提交。", "task_id": task_id}

class BulkDeleteEpisodesRequest(models.BaseModel):
    episode_ids: List[int]

@router.post("/library/episodes/delete-bulk", status_code=status.HTTP_202_ACCEPTED, summary="提交批量删除分集的任务")
async def delete_bulk_episodes(
    request_data: BulkDeleteEpisodesRequest,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来批量删除多个分集。"""
    if not request_data.episode_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Episode IDs list cannot be empty.")

    task_title = f"批量删除 {len(request_data.episode_ids)} 个分集"
    
    # 注意：这里我们将整个列表传递给任务
    task_coro = lambda callback: delete_bulk_episodes_task(request_data.episode_ids, pool, callback)
    
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了批量删除 {len(request_data.episode_ids)} 个分集的任务 (Task ID: {task_id})。")
    return {"message": task_title + "的任务已提交。", "task_id": task_id}


@router.put("/library/source/{source_id}/favorite", status_code=status.HTTP_204_NO_CONTENT, summary="切换数据源的精确标记状态")
async def toggle_source_favorite(
    source_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """切换指定数据源的精确标记状态。一个作品只能有一个精确标记的源。"""
    toggled = await crud.toggle_source_favorite_status(pool, source_id)
    if not toggled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return

@router.put("/library/source/{source_id}/toggle-incremental-refresh", status_code=status.HTTP_204_NO_CONTENT, summary="切换数据源的定时增量更新状态")
async def toggle_source_incremental_refresh(
    source_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """切换指定数据源的定时增量更新的启用/禁用状态。"""
    toggled = await crud.toggle_source_incremental_refresh(pool, source_id)
    if not toggled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    logger.info(f"用户 '{current_user.username}' 切换了源 ID {source_id} 的定时增量更新状态。")

class SourceDetail(models.BaseModel):
    source_id: int
    provider_name: str
    media_id: str
    is_favorited: bool
    incremental_refresh_enabled: bool
    created_at: datetime

@router.get("/library/anime/{anime_id}/sources", response_model=List[SourceDetail], summary="获取作品的所有数据源")
async def get_anime_sources_for_anime(
    anime_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取指定作品关联的所有数据源列表。"""
    return await crud.get_anime_sources(pool, anime_id)

@router.get("/library/source/{source_id}/episodes", response_model=List[models.EpisodeDetail], summary="获取数据源的所有分集")
async def get_source_episodes(
    source_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取指定数据源下的所有已收录分集列表。"""
    return await crud.get_episodes_for_source(pool, source_id)

@router.put("/library/episode/{episode_id}", status_code=status.HTTP_204_NO_CONTENT, summary="编辑分集信息")
async def edit_episode_info(
    episode_id: int,
    update_data: models.EpisodeInfoUpdate,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """更新指定分集的标题、集数和链接。"""
    try:
        updated = await crud.update_episode_info(pool, episode_id, update_data)
        if not updated:
            logger.warning(f"尝试更新一个不存在的分集 (ID: {episode_id})，操作被拒绝。")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
        logger.info(f"用户 '{current_user.username}' 更新了分集 ID: {episode_id} 的信息。")
        return
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

@router.post("/library/source/{source_id}/reorder-episodes", status_code=status.HTTP_202_ACCEPTED, summary="重整指定源的分集顺序")
async def reorder_source_episodes(
    source_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，按当前顺序重新编号指定数据源的所有分集。"""
    source_info = await crud.get_anime_source_info(pool, source_id)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    task_title = f"重整集数: {source_info['title']} ({source_info['provider_name']})"
    task_coro = lambda callback: reorder_episodes_task(source_id, pool, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了重整源 ID: {source_id} 集数的任务 (Task ID: {task_id})。")
    return {"message": f"重整集数任务 '{task_title}' 已提交。", "task_id": task_id}

@router.post("/library/source/{source_id}/incremental-refresh", status_code=status.HTTP_202_ACCEPTED, summary="增量刷新指定源")
async def incremental_refresh_source(
    source_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，增量刷新指定源，抓取比当前已收录分集多一集的弹幕"""
    source_info = await crud.get_anime_source_info(pool, source_id)
    if not source_info or not source_info.get("provider_name") or not source_info.get("media_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anime not found or missing source information for refresh.")

    # 获取当前最新分集
    eps = await crud.get_episodes_for_source(pool, source_id)
    if len(eps) > 0:
        latest_episode_index = sorted(eps, key=lambda x: x['episode_index'])[-1]['episode_index']
    else:
        latest_episode_index = 0

    next_episode_index = latest_episode_index + 1
    logger.info(f"用户 '{current_user.username}' 为番剧 '{source_info['title']}' (源ID: {source_id}) 启动了增量刷新任务。")

    # 从新集信息创建任务
    task_coro = lambda callback: incremental_refresh_task(source_id, next_episode_index, pool, scraper_manager, task_manager, callback, source_info["title"])
    task_id, _ = await task_manager.submit_task(task_coro, f"增量刷新: {source_info['title']} - 尝试获取第{next_episode_index}集")

    return {"message": f"番剧 '{source_info['title']}' 的增量刷新任务已提交，尝试获取第{next_episode_index}集。", "task_id": task_id}

async def incremental_refresh_task(source_id: int, next_episode_index: int, pool: aiomysql.Pool, manager: ScraperManager, task_manager: TaskManager, progress_callback: Callable, anime_title: str):
    """后台任务：增量刷新一个已存在的番剧。"""
    logger.info(f"开始增量刷新源 ID: {source_id}，尝试获取第{next_episode_index}集")
    source_info = await crud.get_anime_source_info(pool, source_id)
    if not source_info:
        progress_callback(100, "失败: 找不到源信息")
        logger.error(f"刷新失败：在数据库中找不到源 ID: {source_id}")
        return
    try:
        # 重新执行通用导入逻辑, 只导入指定的一集
        await generic_import_task(
            provider=source_info["provider_name"],
            media_id=source_info["media_id"],
            anime_title=anime_title,
            media_type=source_info["type"],
            season=source_info.get("season", 1),
            current_episode_index=next_episode_index,
            image_url=None,
            douban_id=None, tmdb_id=source_info.get("tmdb_id"), 
            imdb_id=None, tvdb_id=None,
            progress_callback=progress_callback,
            pool=pool,
            manager=manager,
            task_manager=task_manager)
    except Exception as e:
        logger.error(f"增量刷新源任务 (ID: {source_id}) 失败: {e}", exc_info=True)
        raise


@router.delete("/library/episode/{episode_id}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除指定分集的任务")
async def delete_episode_from_source(
    episode_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来删除一个分集及其所有关联的弹幕。"""
    episode_info = await crud.get_episode_for_refresh(pool, episode_id)
    if not episode_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")

    task_title = f"删除分集: {episode_info['title']}"
    task_coro = lambda callback: delete_episode_task(episode_id, pool, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了删除分集 ID: {episode_id} 的任务 (Task ID: {task_id})。")
    return {"message": f"删除分集 '{episode_info['title']}' 的任务已提交。", "task_id": task_id}

@router.post("/library/episode/{episode_id}/refresh", status_code=status.HTTP_202_ACCEPTED, summary="刷新单个分集的弹幕")
async def refresh_single_episode(
    episode_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """为指定分集启动一个后台任务，重新获取其弹幕。"""
    # 检查分集是否存在，以提供更友好的404错误
    episode = await crud.get_episode_for_refresh(pool, episode_id)
    if not episode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
    
    logger.info(f"用户 '{current_user.username}' 请求刷新分集 ID: {episode_id} ({episode['title']})")
    
    task_coro = lambda callback: refresh_episode_task(episode_id, pool, scraper_manager, callback)
    task_id, _ = await task_manager.submit_task(task_coro, f"刷新分集: {episode['title']}")

    return {"message": f"分集 '{episode['title']}' 的刷新任务已提交。", "task_id": task_id}

@router.post("/library/source/{source_id}/refresh", status_code=status.HTTP_202_ACCEPTED, summary="全量刷新指定源的弹幕")
async def refresh_anime(
    source_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """为指定数据源启动一个后台任务，删除其所有旧弹幕并从源重新获取。"""
    source_info = await crud.get_anime_source_info(pool, source_id)
    if not source_info or not source_info.get("provider_name") or not source_info.get("media_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anime not found or missing source information for refresh.")
    
    logger.info(f"用户 '{current_user.username}' 为番剧 '{source_info['title']}' (源ID: {source_id}) 启动了全量刷新任务。")
    
    task_coro = lambda callback: full_refresh_task(source_id, pool, scraper_manager, task_manager, callback)
    task_id, _ = await task_manager.submit_task(task_coro, f"刷新: {source_info['title']}")

    return {"message": f"番剧 '{source_info['title']}' 的全量刷新任务已提交。", "task_id": task_id}

@router.delete("/library/anime/{anime_id}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除媒体库中番剧的任务")
async def delete_anime_from_library(
    anime_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来删除一个番剧及其所有关联数据。"""
    # Get title for task name
    anime_details = await crud.get_anime_full_details(pool, anime_id)
    if not anime_details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anime not found")
    
    task_title = f"删除作品: {anime_details['title']}"
    task_coro = lambda callback: delete_anime_task(anime_id, pool, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了删除作品 ID: {anime_id} 的任务 (Task ID: {task_id})。")
    return {"message": f"删除作品 '{anime_details['title']}' 的任务已提交。", "task_id": task_id}

class BulkDeleteRequest(models.BaseModel):
    source_ids: List[int]

@router.post("/library/sources/delete-bulk", status_code=status.HTTP_202_ACCEPTED, summary="提交批量删除数据源的任务")
async def delete_bulk_sources(
    request_data: BulkDeleteRequest,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来批量删除多个数据源。"""
    if not request_data.source_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source IDs list cannot be empty.")

    task_title = f"批量删除 {len(request_data.source_ids)} 个数据源"
    task_coro = lambda callback: delete_bulk_sources_task(request_data.source_ids, pool, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了批量删除 {len(request_data.source_ids)} 个源的任务 (Task ID: {task_id})。")
    return {"message": task_title + "的任务已提交。", "task_id": task_id}

class ScraperSettingWithConfig(models.ScraperSetting):
    configurable_fields: Optional[Dict[str, str]] = None
    is_loggable: bool = False

@router.get("/scrapers", response_model=List[ScraperSettingWithConfig], summary="获取所有搜索源的设置")
async def get_scraper_settings(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """获取所有可用搜索源的列表及其配置（启用状态、顺序、可配置字段）。"""
    settings = await crud.get_all_scraper_settings(pool)
    
    full_settings = []
    for s in settings:
        scraper_class = manager.get_scraper_class(s['provider_name'])
        s_with_config = ScraperSettingWithConfig.model_validate(s)
        if scraper_class:
            s_with_config.is_loggable = getattr(scraper_class, "is_loggable", False)
            # 关键修复：复制类属性以避免修改共享的可变字典
            base_fields = getattr(scraper_class, "configurable_fields", None)
            s_with_config.configurable_fields = base_fields.copy() if base_fields is not None else {}

            # 为当前源动态添加其专属的黑名单配置字段
            blacklist_key = f"{s['provider_name']}_episode_blacklist_regex"
            s_with_config.configurable_fields[blacklist_key] = "分集标题黑名单 (正则)"
        full_settings.append(s_with_config)
            
    return full_settings

@router.get("/metadata-sources", response_model=List[Dict[str, Any]], summary="获取所有元数据源的设置")
async def get_metadata_source_settings(
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """获取所有元数据源及其当前状态（配置、连接性等）。"""
    return await manager.get_sources_with_status()

@router.put("/metadata-sources", status_code=status.HTTP_204_NO_CONTENT, summary="更新元数据源的设置")
async def update_metadata_source_settings(
    settings: List[models.MetadataSourceSettingUpdate],
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """批量更新元数据源的启用状态、辅助搜索状态和显示顺序。"""
    # 修正：恢复使用专用的 `metadata_sources` 表来存储设置，
    # 这将调用 `crud.py` 中正确的函数，并解决之前遇到的状态保存问题。
    await crud.update_metadata_sources_settings(pool, settings)
    logger.info(f"用户 '{current_user.username}' 更新了元数据源设置。")

@router.put("/scrapers", status_code=status.HTTP_204_NO_CONTENT, summary="更新搜索源的设置")
async def update_scraper_settings(
    settings: List[models.ScraperSetting],
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """批量更新搜索源的启用状态和显示顺序。"""
    await crud.update_scrapers_settings(pool, settings)
    # 更新数据库后，触发 ScraperManager 重新加载搜索源
    await manager.load_and_sync_scrapers()
    logger.info(f"用户 '{current_user.username}' 更新了搜索源设置，已重新加载。")
    return

class ProxyTestResult(BaseModel):
    status: str  # 'success' or 'failure'
    latency: Optional[float] = None # in ms
    error: Optional[str] = None

class ProxySettingsResponse(BaseModel):
    proxy_protocol: str
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    proxy_enabled: bool

class ProxySettingsUpdate(BaseModel):
    proxy_protocol: str
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    proxy_enabled: bool

@router.get("/config/proxy", response_model=ProxySettingsResponse, summary="获取代理配置")

async def get_proxy_settings(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取全局代理配置。"""
    proxy_url_task = crud.get_config_value(pool, "proxy_url", "")
    proxy_enabled_task = crud.get_config_value(pool, "proxy_enabled", "false")
    proxy_url, proxy_enabled_str = await asyncio.gather(proxy_url_task, proxy_enabled_task)

    proxy_enabled = proxy_enabled_str.lower() == 'true'
    
    # Parse the URL into components
    protocol, host, port, username, password = "http", None, None, None, None
    if proxy_url:
        try:
            p = urlparse(proxy_url)
            protocol = p.scheme or "http"
            host = p.hostname
            port = p.port
            username = unquote(p.username) if p.username else None
            password = unquote(p.password) if p.password else None
        except Exception as e:
            logger.error(f"解析存储的代理URL '{proxy_url}' 失败: {e}")

    return ProxySettingsResponse(
        proxy_protocol=protocol,
        proxy_host=host,
        proxy_port=port,
        proxy_username=username,
        proxy_password=password,
        proxy_enabled=proxy_enabled
    )

@router.put("/config/proxy", status_code=status.HTTP_204_NO_CONTENT, summary="更新代理配置")
async def update_proxy_settings(
    payload: ProxySettingsUpdate,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """更新全局代理配置。"""
    proxy_url = ""
    if payload.proxy_host and payload.proxy_port:
        # URL-encode username and password to handle special characters like '@'
        userinfo = ""
        if payload.proxy_username:
            userinfo = quote(payload.proxy_username)
            if payload.proxy_password:
                userinfo += ":" + quote(payload.proxy_password)
            userinfo += "@"
        
        proxy_url = f"{payload.proxy_protocol}://{userinfo}{payload.proxy_host}:{payload.proxy_port}"

    tasks = [
        crud.update_config_value(pool, "proxy_url", proxy_url),
        crud.update_config_value(pool, "proxy_enabled", str(payload.proxy_enabled).lower())
    ]
    await asyncio.gather(*tasks)
    logger.info(f"用户 '{current_user.username}' 更新了代理配置。")

class ProxyTestRequest(BaseModel):
    proxy_url: Optional[str] = None

class FullProxyTestResponse(BaseModel):
    proxy_connectivity: ProxyTestResult
    target_sites: Dict[str, ProxyTestResult]

@router.post("/proxy/test", response_model=FullProxyTestResponse, summary="测试代理连接和延迟")
async def test_proxy_latency(
    request: ProxyTestRequest,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """测试代理到常用域名的连接延迟。如果未提供代理URL，则测试直接连接。"""
    proxy_url = request.proxy_url
    # 修正：使用旧的 'proxy' 参数以兼容旧版 httpx
    proxy_to_use = proxy_url if proxy_url else None

    # Test 1: Proxy Connectivity
    proxy_connectivity_result: ProxyTestResult
    # 使用一个已知的高可用、轻量级端点进行测试
    test_url_google = "http://www.google.com/generate_204"

    if not proxy_url:
        proxy_connectivity_result = ProxyTestResult(status="skipped", error="未配置代理，跳过测试")
    else:
        try:
            async with httpx.AsyncClient(proxy=proxy_to_use, timeout=10.0, follow_redirects=False) as client:
                start_time = time.time()
                response = await client.get(test_url_google)
                latency = (time.time() - start_time) * 1000
                # 204 No Content 是成功的标志
                if response.status_code == 204:
                    proxy_connectivity_result = ProxyTestResult(status="success", latency=latency)
                else:
                    proxy_connectivity_result = ProxyTestResult(
                        status="failure",
                        error=f"连接成功但状态码异常: {response.status_code}"
                    )
        except Exception as e:
            proxy_connectivity_result = ProxyTestResult(status="failure", error=str(e))

    # Test 2: Target Site Reachability
    target_sites_results: Dict[str, ProxyTestResult] = {}
    test_domains = [
        "https://api.bilibili.com", "https://www.iqiyi.com", "https://v.qq.com",
        "https://youku.com", "https://www.mgtv.com", "https://ani.gamer.com.tw",
        "https://api.themoviedb.org", "https://api.bgm.tv", "https://movie.douban.com"
    ]
    
    async def test_domain(domain: str, client: httpx.AsyncClient) -> tuple[str, ProxyTestResult]:
        try:
            start_time = time.time()
            # 使用 HEAD 请求以提高效率，我们只关心连通性
            await client.head(domain, timeout=10.0)
            latency = (time.time() - start_time) * 1000
            return domain, ProxyTestResult(status="success", latency=latency)
        except Exception as e:
            # 简化错误信息，避免在UI上显示过长的堆栈跟踪
            error_str = f"{type(e).__name__}"
            return domain, ProxyTestResult(status="failure", error=error_str)

    async with httpx.AsyncClient(proxy=proxy_to_use, timeout=10.0) as client:
        tasks = [test_domain(domain, client) for domain in test_domains]
        results = await asyncio.gather(*tasks)
        for domain, result in results:
            target_sites_results[domain] = result

    return FullProxyTestResponse(
        proxy_connectivity=proxy_connectivity_result,
        target_sites=target_sites_results
    )

@router.get("/scrapers/{provider_name}/config", response_model=Dict[str, str], summary="获取指定搜索源的配置")
async def get_scraper_config(
    provider_name: str,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    scraper_class = manager.get_scraper_class(provider_name)
    is_configurable = hasattr(scraper_class, 'configurable_fields') and scraper_class.configurable_fields
    is_loggable = getattr(scraper_class, 'is_loggable', False)

    if not scraper_class or not (is_configurable or is_loggable):
        raise HTTPException(status_code=404, detail="该搜索源不可配置或不存在。")
    
    config_keys = []
    if is_configurable:
        config_keys.extend(scraper_class.configurable_fields.keys())
    # 如果源是可记录日志的，也获取其日志配置
    if is_loggable:
        config_keys.append(f"scraper_{provider_name}_log_responses")
    
    # 新增：总是获取该源的自定义黑名单配置
    blacklist_key = f"{provider_name}_episode_blacklist_regex"
    config_keys.append(blacklist_key)

    if not config_keys: return {}
    tasks = [crud.get_config_value(pool, key, "") for key in config_keys]
    values = await asyncio.gather(*tasks)
    
    return dict(zip(config_keys, values))

@router.put("/scrapers/{provider_name}/config", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定搜索源的配置")
async def update_scraper_config(
    provider_name: str,
    payload: Dict[str, str],
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    scraper_class = manager.get_scraper_class(provider_name)
    is_configurable = hasattr(scraper_class, 'configurable_fields') and scraper_class.configurable_fields
    is_loggable = getattr(scraper_class, 'is_loggable', False)

    if not scraper_class or not (is_configurable or is_loggable):
        raise HTTPException(status_code=404, detail="该搜索源不可配置或不存在。")

    allowed_keys = []
    if is_configurable:
        allowed_keys.extend(scraper_class.configurable_fields.keys())
    # 如果源是可记录日志的，也允许更新其日志配置
    if is_loggable:
        allowed_keys.append(f"scraper_{provider_name}_log_responses")
    # 允许更新通用的黑名单配置
    allowed_keys.append(f"{provider_name}_episode_blacklist_regex")

    tasks = [crud.update_config_value(pool, key, value or "") for key, value in payload.items() if key in allowed_keys]
    
    if tasks:
        await asyncio.gather(*tasks)
        logger.info(f"用户 '{current_user.username}' 更新了搜索源 '{provider_name}' 的配置。")

@router.get("/logs", response_model=List[str], summary="获取最新的服务器日志")
async def get_server_logs(current_user: models.User = Depends(security.get_current_user)):
    """获取存储在内存中的最新日志条目。"""
    return get_logs()

@router.get("/config/tmdb", response_model=Dict[str, str], summary="获取TMDB配置")
async def get_tmdb_settings(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取所有TMDB相关的配置。"""
    keys = ["tmdb_api_key", "tmdb_api_base_url", "tmdb_image_base_url"]
    tasks = [crud.get_config_value(pool, key, "") for key in keys]
    values = await asyncio.gather(*tasks)
    return dict(zip(keys, values))

@router.post("/scrapers/{provider_name}/actions/{action_name}", summary="执行搜索源的自定义操作")
async def execute_scraper_action(
    provider_name: str,
    action_name: str,
    payload: Dict[str, Any] = None, # FastAPI will parse JSON body into a dict
    current_user: models.User = Depends(security.get_current_user),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """
    执行指定搜索源的特定操作。
    例如，Bilibili的登录流程可以通过调用 'get_login_info', 'generate_qrcode', 'poll_login' 等操作来驱动。
    """
    try:
        scraper = manager.get_scraper(provider_name)
        result = await scraper.execute_action(action_name, payload or {})
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"执行搜索源 '{provider_name}' 的操作 '{action_name}' 时出错: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="执行操作时发生内部错误。")

@router.put("/config/tmdb", status_code=status.HTTP_204_NO_CONTENT, summary="更新TMDB配置")
async def update_tmdb_settings(
    payload: Dict[str, str],
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """批量更新TMDB相关的配置。"""
    tasks = []
    for key, value in payload.items():
        if key in ["tmdb_api_key", "tmdb_api_base_url", "tmdb_image_base_url"]:
            tasks.append(crud.update_config_value(pool, key, value or ""))
    await asyncio.gather(*tasks)
    logger.info(f"用户 '{current_user.username}' 更新了 TMDB 配置。")
    
@router.get("/config/bangumi", response_model=Dict[str, str], summary="获取Bangumi配置")
async def get_bangumi_settings(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取Bangumi OAuth相关的配置。"""
    keys = ["bangumi_client_id", "bangumi_client_secret"]
    tasks = [crud.get_config_value(pool, key, "") for key in keys]
    values = await asyncio.gather(*tasks)
    return dict(zip(keys, values))

@router.put("/config/bangumi", status_code=status.HTTP_204_NO_CONTENT, summary="更新Bangumi配置")
async def update_bangumi_settings(
    payload: Dict[str, str],
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """批量更新Bangumi OAuth相关的配置。"""
    tasks = []
    for key, value in payload.items():
        if key in ["bangumi_client_id", "bangumi_client_secret"]:
            tasks.append(crud.update_config_value(pool, key, value or ""))
    if tasks:
        await asyncio.gather(*tasks)
    logger.info(f"用户 '{current_user.username}' 更新了 Bangumi 配置。")

@router.post("/cache/clear", status_code=status.HTTP_200_OK, summary="清除所有缓存")

async def clear_all_caches(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """清除数据库中存储的所有缓存数据（如搜索结果、分集列表）。"""
    deleted_count = await crud.clear_all_cache(pool)
    logger.info(f"用户 '{current_user.username}' 清除了所有缓存，共 {deleted_count} 条。")
    return {"message": f"成功清除了 {deleted_count} 条缓存记录。"}

@router.get("/tasks", response_model=List[models.TaskInfo], summary="获取所有后台任务的状态")
async def get_all_tasks(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    search: Optional[str] = Query(None, description="按标题搜索"),
    status: Optional[str] = Query("all", description="按状态过滤: all, in_progress, completed")
):
    """获取后台任务的列表和状态，支持搜索和过滤。"""
    tasks = await crud.get_tasks_from_history(pool, search, status)
    return [models.TaskInfo.model_validate(t) for t in tasks]

@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除一个历史任务")
async def delete_task_from_history_endpoint(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """从历史记录中删除一个任务。如果任务正在运行或暂停，会先尝试中止它。"""
    task = await crud.get_task_from_history_by_id(pool, task_id)
    if not task:
        # 如果任务不存在，直接返回成功，因为最终状态是一致的
        return

    status = task['status']

    if status == TaskStatus.PENDING:
        await task_manager.cancel_pending_task(task_id)
    elif status in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
        aborted = await task_manager.abort_current_task(task_id)
        if not aborted:
            # 这可能是一个竞态条件：在我们检查和中止之间，任务可能已经完成。
            # 重新检查数据库中的状态以确认。
            task_after_check = await crud.get_task_from_history_by_id(pool, task_id)
            if task_after_check and task_after_check['status'] in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
                # 如果它仍然在运行/暂停，说明中止失败，可能因为它不是当前任务。
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="中止任务失败，可能它不是当前正在执行的任务。")
            logger.info(f"任务 {task_id} 在中止前已完成，将直接删除历史记录。")

    deleted = await crud.delete_task_from_history(pool, task_id)
    if not deleted:
        # 这不是一个严重错误，可能意味着任务在处理过程中已被删除。
        logger.info(f"在尝试删除时，任务 {task_id} 已不存在于历史记录中。")
        return
    logger.info(f"用户 '{current_user.username}' 删除了任务 ID: {task_id} (原状态: {status})。")
    return

@router.get("/tokens", response_model=List[models.ApiTokenInfo], summary="获取所有弹幕API Token")
async def get_all_api_tokens(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取所有为第三方播放器创建的 API Token。"""
    tokens = await crud.get_all_api_tokens(pool)
    return [models.ApiTokenInfo.model_validate(t) for t in tokens]

@router.post("/tokens", response_model=models.ApiTokenInfo, status_code=status.HTTP_201_CREATED, summary="创建一个新的API Token")
async def create_new_api_token(
    token_data: models.ApiTokenCreate,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """创建一个新的、随机的 API Token。"""
    # 生成一个由大小写字母和数字组成的20位随机字符串
    alphabet = string.ascii_letters + string.digits
    new_token_str = ''.join(secrets.choice(alphabet) for _ in range(20))
    token_id = await crud.create_api_token(
        pool, token_data.name, new_token_str, token_data.validity_period
    )
    # 重新从数据库获取以包含所有字段
    new_token = await crud.get_api_token_by_id(pool, token_id) # 假设这个函数存在
    return models.ApiTokenInfo.model_validate(new_token)

@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除一个API Token")
async def delete_api_token(
    token_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """根据ID删除一个 API Token。"""
    deleted = await crud.delete_api_token(pool, token_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return

@router.put("/tokens/{token_id}/toggle", status_code=status.HTTP_204_NO_CONTENT, summary="切换API Token的启用状态")
async def toggle_api_token_status(
    token_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """切换指定 API Token 的启用/禁用状态。"""
    toggled = await crud.toggle_api_token(pool, token_id)
    if not toggled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return

@router.get("/config/{config_key}", response_model=Dict[str, str], summary="获取指定配置项的值")
async def get_config_item(
    config_key: str,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """获取数据库中单个配置项的值。"""
    value = await crud.get_config_value(pool, config_key, "") # 默认为空字符串
    return {"key": config_key, "value": value}

@router.put("/config/{config_key}", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定配置项的值")
async def update_config_item(
    config_key: str,
    payload: Dict[str, str],
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """更新数据库中单个配置项的值。"""
    value = payload.get("value")
    if value is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing 'value' in request body")
    
    await crud.update_config_value(pool, config_key, value)
    logger.info(f"用户 '{current_user.username}' 更新了配置项 '{config_key}'。")

@router.post("/config/webhook_api_key/regenerate", response_model=Dict[str, str], summary="重新生成Webhook API Key")
async def regenerate_webhook_api_key(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    """生成一个新的、随机的Webhook API Key并保存到数据库。"""
    alphabet = string.ascii_letters + string.digits
    new_key = ''.join(secrets.choice(alphabet) for _ in range(20))
    await crud.update_config_value(pool, "webhook_api_key", new_key)
    logger.info(f"用户 '{current_user.username}' 重新生成了 Webhook API Key。")
    return {"key": "webhook_api_key", "value": new_key}

@router.get("/ua-rules", response_model=List[models.UaRule], summary="获取所有UA规则")
async def get_ua_rules(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    rules = await crud.get_ua_rules(pool)
    return [models.UaRule.model_validate(r) for r in rules]

class UaRuleCreate(models.BaseModel):
    ua_string: str

@router.post("/ua-rules", response_model=models.UaRule, status_code=201, summary="添加UA规则")
async def add_ua_rule(
    rule_data: UaRuleCreate,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    try:
        rule_id = await crud.add_ua_rule(pool, rule_data.ua_string)
        # This is a bit inefficient but ensures we return the full object
        rules = await crud.get_ua_rules(pool)
        new_rule = next((r for r in rules if r['id'] == rule_id), None)
        return models.UaRule.model_validate(new_rule)
    except aiomysql.IntegrityError:
        raise HTTPException(status_code=409, detail="该UA规则已存在。")

@router.delete("/ua-rules/{rule_id}", status_code=204, summary="删除UA规则")
async def delete_ua_rule(
    rule_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    deleted = await crud.delete_ua_rule(pool, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="找不到指定的规则ID。")

@router.get("/tokens/{token_id}/logs", response_model=List[models.TokenAccessLog], summary="获取Token的访问日志")
async def get_token_logs(
    token_id: int,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    logs = await crud.get_token_access_logs(pool, token_id)
    return [models.TokenAccessLog.model_validate(log) for log in logs]

@router.get(
    "/comment/{episode_id}",
    response_model=models.CommentResponse,
    summary="获取指定分集的弹幕",
)
async def get_comments(
    episode_id: int,
    pool=Depends(get_db_pool)
):
    # 检查episode是否存在，如果不存在则返回404
    if not await crud.check_episode_exists(pool, episode_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")

    comments_data = await crud.fetch_comments(pool, episode_id)
    
    comments = [models.Comment(cid=item["cid"], p=item["p"], m=item["m"]) for item in comments_data]
    return models.CommentResponse(count=len(comments), comments=comments)

@router.get("/webhooks/available", response_model=List[str], summary="获取所有可用的Webhook类型")
async def get_available_webhook_types(
    current_user: models.User = Depends(security.get_current_user),
    webhook_manager: WebhookManager = Depends(get_webhook_manager)
):
    """获取所有已成功加载的、可供用户选择的Webhook处理器类型。"""
    return webhook_manager.get_available_handlers()

async def delete_anime_task(anime_id: int, pool: aiomysql.Pool, progress_callback: Callable):
    """Background task to delete an anime and all its related data."""
    await progress_callback(0, "开始删除...")
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            try:
                await conn.begin()

                # 1. 获取该作品关联的所有源ID
                await progress_callback(10, "正在查找关联的数据源...")
                await cursor.execute("SELECT id FROM anime_sources WHERE anime_id = %s", (anime_id,))
                source_ids = [row[0] for row in await cursor.fetchall()]

                if source_ids:
                    # 2. 获取所有源关联的所有分集ID
                    await progress_callback(20, "正在查找关联的分集...")
                    format_strings_sources = ','.join(['%s'] * len(source_ids))
                    await cursor.execute(f"SELECT id FROM episode WHERE source_id IN ({format_strings_sources})", tuple(source_ids))
                    episode_ids = [row[0] for row in await cursor.fetchall()]

                    if episode_ids:
                        # 3. 删除所有分集关联的弹幕
                        await progress_callback(40, "正在删除弹幕...")
                        format_strings_episodes = ','.join(['%s'] * len(episode_ids))
                        await cursor.execute(f"DELETE FROM comment WHERE episode_id IN ({format_strings_episodes})", tuple(episode_ids))
                        
                        # 4. 删除所有分集
                        await progress_callback(60, "正在删除分集...")
                        await cursor.execute(f"DELETE FROM episode WHERE id IN ({format_strings_episodes})", tuple(episode_ids))
                    
                    # 5. 删除所有源记录
                    await progress_callback(80, "正在删除数据源...")
                    await cursor.execute(f"DELETE FROM anime_sources WHERE id IN ({format_strings_sources})", tuple(source_ids))

                # 6. 删除元数据 (别名表有级联删除，元数据表没有，需要手动删除)
                await progress_callback(90, "正在删除元数据...")
                await cursor.execute("DELETE FROM anime_metadata WHERE anime_id = %s", (anime_id,))

                # 7. 删除作品本身
                await progress_callback(95, "正在删除作品条目...")
                affected_rows = await cursor.execute("DELETE FROM anime WHERE id = %s", (anime_id,))
                
                await conn.commit()

                if affected_rows > 0:
                    raise TaskSuccess("删除成功。")
                else:
                    raise TaskSuccess("作品未找到，无需删除。")
            except Exception as e:
                await conn.rollback()
                logger.error(f"删除作品任务 (ID: {anime_id}) 失败: {e}", exc_info=True)
                raise

async def delete_source_task(source_id: int, pool: aiomysql.Pool, progress_callback: Callable):
    """Background task to delete a source and all its related data."""
    progress_callback(0, "开始删除...")
    try:
        # Re-implementing crud.delete_anime_source with progress
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await conn.begin()
                await progress_callback(10, "正在检查数据源...")
                await cursor.execute("SELECT 1 FROM anime_sources WHERE id = %s", (source_id,))
                if not await cursor.fetchone():
                    raise TaskSuccess("数据源未找到，无需删除。")
                await progress_callback(20, "正在查找关联的分集...")
                await cursor.execute("SELECT id FROM episode WHERE source_id = %s", (source_id,))
                episode_ids = [row[0] for row in await cursor.fetchall()]
                if episode_ids:
                    await progress_callback(40, "正在删除弹幕...")
                    format_strings = ','.join(['%s'] * len(episode_ids))
                    await cursor.execute(f"DELETE FROM comment WHERE episode_id IN ({format_strings})", tuple(episode_ids))
                    await progress_callback(70, "正在删除分集...")
                    await cursor.execute(f"DELETE FROM episode WHERE id IN ({format_strings})", tuple(episode_ids))
                await progress_callback(90, "正在删除数据源记录...")
                await cursor.execute("DELETE FROM anime_sources WHERE id = %s", (source_id,))
                await conn.commit()
        raise TaskSuccess("删除成功。")
    except Exception as e:
        logger.error(f"删除源任务 (ID: {source_id}) 失败: {e}", exc_info=True)
        raise

async def delete_episode_task(episode_id: int, pool: aiomysql.Pool, progress_callback: Callable):
    """Background task to delete an episode and its comments."""
    progress_callback(0, "开始删除...")
    try:
        # Re-implementing crud.delete_episode with progress
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await conn.begin()
                await progress_callback(20, "正在检查分集...")
                await cursor.execute("SELECT 1 FROM episode WHERE id = %s", (episode_id,))
                if not await cursor.fetchone():
                    raise TaskSuccess("分集未找到，无需删除。")
                await progress_callback(50, "正在删除弹幕...")
                await cursor.execute("DELETE FROM comment WHERE episode_id = %s", (episode_id,))
                await progress_callback(80, "正在删除分集记录...")
                await cursor.execute("DELETE FROM episode WHERE id = %s", (episode_id,))
                await conn.commit()
        raise TaskSuccess("删除成功。")
    except Exception as e:
        logger.error(f"删除分集任务 (ID: {episode_id}) 失败: {e}", exc_info=True)
        raise

async def delete_bulk_episodes_task(episode_ids: List[int], pool: aiomysql.Pool, progress_callback: Callable):
    """后台任务：批量删除多个分集。"""
    total = len(episode_ids)
    deleted_count = 0
    for i, episode_id in enumerate(episode_ids):
        progress = int((i / total) * 100)
        await progress_callback(progress, f"正在删除分集 {i+1}/{total} (ID: {episode_id})...")
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await conn.begin()
                    await cursor.execute("DELETE FROM comment WHERE episode_id = %s", (episode_id,))
                    affected_rows = await cursor.execute("DELETE FROM episode WHERE id = %s", (episode_id,))
                    await conn.commit()
                    if affected_rows > 0:
                        deleted_count += 1
        except Exception as e:
            logger.error(f"批量删除分集任务中，删除分集 (ID: {episode_id}) 失败: {e}", exc_info=True)
    raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个。")

async def generic_import_task(
    provider: str, # noqa: F821
    media_id: str,
    anime_title: str,
    media_type: str,
    season: int,
    current_episode_index: Optional[int],
    image_url: Optional[str],
    douban_id: Optional[str],
    tmdb_id: Optional[str],
    imdb_id: Optional[str],
    tvdb_id: Optional[str],
    progress_callback: Callable,
    pool: aiomysql.Pool, 
    manager: ScraperManager, 
    task_manager: TaskManager
):
    """
    后台任务：执行从指定数据源导入弹幕的完整流程。
    """
    try:
        scraper = manager.get_scraper(provider)

        # 统一将标题中的英文冒号替换为中文冒号，作为写入数据库前的最后保障
        normalized_title = anime_title.replace(":", "：")

        # 步骤 1: 获取所有分集信息
        await progress_callback(10, "正在获取分集列表...")
        episodes = await scraper.get_episodes(
            media_id,
            target_episode_index=current_episode_index,
            db_media_type=media_type
        )
        if not episodes:
            msg = f"未能找到第 {current_episode_index} 集。" if current_episode_index else "未能获取到任何分集。"
            logger.warning(f"任务终止: {msg} (provider='{provider}', media_id='{media_id}')")
            raise TaskSuccess(msg)

        # 如果是电影，即使返回了多个版本（如原声、国语），也只处理第一个
        if media_type == "movie" and episodes:
            logger.info(f"检测到媒体类型为电影，将只处理第一个分集 '{episodes[0].title}'。")
            episodes = episodes[:1]

        # 步骤 2: 循环处理分集，在首次成功获取弹幕后再创建数据库主条目
        anime_id: Optional[int] = None
        source_id: Optional[int] = None
        total_comments_added = 0
        total_episodes = len(episodes)

        for i, episode in enumerate(episodes):
            logger.info(f"--- 开始处理分集 {i+1}/{total_episodes}: '{episode.title}' (ID: {episode.episodeId}) ---")
            base_progress = 10 + int((i / total_episodes) * 85) # 10% for setup, 85% for processing, 5% for final
            await progress_callback(base_progress, f"正在处理: {episode.title} ({i+1}/{total_episodes})")

            async def sub_progress_callback(danmaku_progress: int, danmaku_description: str):
                current_total_progress = base_progress + (danmaku_progress / 100) * (85 / total_episodes)
                await progress_callback(current_total_progress, f"处理: {episode.title} - {danmaku_description}")

            comments = await scraper.get_comments(episode.episodeId, progress_callback=sub_progress_callback)

            # 只有在确实有弹幕需要写入时，才创建番剧和源的记录
            if comments and anime_id is None:
                logger.info("首次成功获取弹幕，正在创建数据库主条目...")
                await progress_callback(base_progress + 1, "正在创建数据库主条目...")
                # 在创建主条目前，先下载并缓存图片
                local_image_path = await download_image(image_url, pool, provider)
                # 修正：将 local_image_path 传递给 get_or_create_anime
                anime_id = await crud.get_or_create_anime(pool, normalized_title, media_type, season, image_url, local_image_path)
                await crud.update_metadata_if_empty(pool, anime_id, tmdb_id, imdb_id, tvdb_id, douban_id)
                source_id = await crud.link_source_to_anime(pool, anime_id, provider, media_id)
                logger.info(f"主条目创建完成 (Anime ID: {anime_id}, Source ID: {source_id})。")

            # 如果主条目已创建（意味着至少有一集有弹幕），则处理当前分集
            if anime_id and source_id:
                episode_db_id = await crud.create_episode_if_not_exists(pool, anime_id, source_id, episode.episodeIndex, episode.title, episode.url, episode.episodeId)
                if not comments:
                    logger.info(f"分集 '{episode.title}' (DB ID: {episode_db_id}) 未找到弹幕，但已创建分集记录。")
                    continue
                added_count = await crud.bulk_insert_comments(pool, episode_db_id, comments)
                total_comments_added += added_count
                logger.info(f"分集 '{episode.title}' (DB ID: {episode_db_id}) 新增 {added_count} 条弹幕。")
            else:
                # 如果 anime_id 仍然是 None，说明到目前为止还没有一集成功获取到弹幕
                logger.info(f"分集 '{episode.title}' 未找到弹幕，跳过创建主条目。")

        if total_comments_added == 0:
            raise TaskSuccess("导入完成，但未找到任何新弹幕。")
        else:
            raise TaskSuccess(f"导入完成，共新增 {total_comments_added} 条弹幕。")
    except TaskSuccess:
        raise # 重新抛出以被 TaskManager 正确处理
    except Exception as e:
        logger.error(f"导入任务发生严重错误: {e}", exc_info=True)
        raise  # 重新抛出异常，以便任务管理器能捕获并标记任务为“失败”
    
async def edited_import_task(
    request_data: "EditedImportRequest",
    progress_callback: Callable,
    pool: aiomysql.Pool,
    manager: ScraperManager
):
    """后台任务：处理编辑后的导入请求。"""
    try:
        scraper = manager.get_scraper(request_data.provider)
        normalized_title = request_data.anime_title.replace(":", "：")
        
        episodes = request_data.episodes
        if not episodes:
            raise TaskSuccess("没有提供任何分集，任务结束。")

        anime_id: Optional[int] = None
        source_id: Optional[int] = None
        total_comments_added = 0
        total_episodes = len(episodes)

        for i, episode in enumerate(episodes):
            await progress_callback(10 + int((i / total_episodes) * 85), f"正在处理: {episode.title} ({i+1}/{total_episodes})")
            # 修正：当不需要子进度回调时，应传递 None 或不传递该参数。
            # 之前传递的 lambda p, d: None 是一个同步函数，对其进行 await 会导致 TypeError。
            # 直接调用而不传递 progress_callback 会使用其默认值 None，从而修复此问题。
            comments = await scraper.get_comments(episode.episodeId)

            if comments and anime_id is None:
                local_image_path = await download_image(request_data.image_url, pool, request_data.provider)
                anime_id = await crud.get_or_create_anime(pool, normalized_title, request_data.media_type, request_data.season, request_data.image_url, local_image_path)
                await crud.update_metadata_if_empty(pool, anime_id, request_data.tmdb_id, None, None, request_data.douban_id)
                source_id = await crud.link_source_to_anime(pool, anime_id, request_data.provider, request_data.media_id)

            if anime_id and source_id:
                episode_db_id = await crud.create_episode_if_not_exists(pool, anime_id, source_id, episode.episodeIndex, episode.title, episode.url, episode.episodeId)
                if comments:
                    total_comments_added += await crud.bulk_insert_comments(pool, episode_db_id, comments)

        if total_comments_added == 0: raise TaskSuccess("导入完成，但未找到任何新弹幕。")
        else: raise TaskSuccess(f"导入完成，共新增 {total_comments_added} 条弹幕。")
    except TaskSuccess: raise
    except Exception as e:
        logger.error(f"编辑后导入任务发生严重错误: {e}", exc_info=True)
        raise

async def full_refresh_task(source_id: int, pool: aiomysql.Pool, manager: ScraperManager, task_manager: TaskManager, progress_callback: Callable):
    """
    后台任务：全量刷新一个已存在的番剧。
    """
    logger.info(f"开始刷新源 ID: {source_id}")
    source_info = await crud.get_anime_source_info(pool, source_id)
    if not source_info:
        progress_callback(100, "失败: 找不到源信息")
        logger.error(f"刷新失败：在数据库中找不到源 ID: {source_id}")
        return
    
    anime_id = source_info["anime_id"]
    # 1. 清空旧数据
    await progress_callback(10, "正在清空旧数据...")
    await crud.clear_source_data(pool, source_id)
    logger.info(f"已清空源 ID: {source_id} 的旧分集和弹幕。") # image_url 在这里不会被传递，因为刷新时我们不希望覆盖已有的海报
    # 2. 重新执行通用导入逻辑
    await generic_import_task(
        provider=source_info["provider_name"],
        media_id=source_info["media_id"],
        anime_title=source_info["title"],
        media_type=source_info["type"],
        season=source_info.get("season", 1),
        current_episode_index=None,
        image_url=None,
        douban_id=None, tmdb_id=source_info.get("tmdb_id"), 
        imdb_id=None, tvdb_id=None,
        progress_callback=progress_callback,
        pool=pool,
        manager=manager,
        task_manager=task_manager)

async def delete_bulk_sources_task(source_ids: List[int], pool: aiomysql.Pool, progress_callback: Callable):
    """Background task to delete multiple sources."""
    total = len(source_ids)
    deleted_count = 0
    for i, source_id in enumerate(source_ids):
        progress = int((i / total) * 100)
        await progress_callback(progress, f"正在删除源 {i+1}/{total} (ID: {source_id})...")
        try:
            # Inlined logic from delete_anime_source
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await conn.begin()
                    await cursor.execute("SELECT 1 FROM anime_sources WHERE id = %s", (source_id,))
                    if not await cursor.fetchone():
                        await conn.rollback()
                        continue # Source not found, skip

                    await cursor.execute("SELECT id FROM episode WHERE source_id = %s", (source_id,))
                    episode_ids = [row[0] for row in await cursor.fetchall()]

                    if episode_ids:
                        format_strings = ','.join(['%s'] * len(episode_ids))
                        await cursor.execute(f"DELETE FROM comment WHERE episode_id IN ({format_strings})", tuple(episode_ids))
                        await cursor.execute(f"DELETE FROM episode WHERE id IN ({format_strings})", tuple(episode_ids))

                    await cursor.execute("DELETE FROM anime_sources WHERE id = %s", (source_id,))
                    await conn.commit()
                    deleted_count += 1
        except Exception as e:
            logger.error(f"批量删除源任务中，删除源 (ID: {source_id}) 失败: {e}", exc_info=True)
            # Continue to the next one
    raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个。")

async def refresh_episode_task(episode_id: int, pool: aiomysql.Pool, manager: ScraperManager, progress_callback: Callable):
    """后台任务：刷新单个分集的弹幕"""
    logger.info(f"开始刷新分集 ID: {episode_id}")
    try:
        await progress_callback(0, "正在获取分集信息...")
        # 1. 获取分集的源信息
        info = await crud.get_episode_provider_info(pool, episode_id)
        if not info or not info.get("provider_name") or not info.get("provider_episode_id"):
            logger.error(f"刷新失败：在数据库中找不到分集 ID: {episode_id} 的源信息")
            progress_callback(100, "失败: 找不到源信息")
            return

        provider_name = info["provider_name"]
        provider_episode_id = info["provider_episode_id"]
        scraper = manager.get_scraper(provider_name)

        # 3. 获取新弹幕并插入
        await progress_callback(30, "正在从源获取新弹幕...")
        
        async def sub_progress_callback(danmaku_progress: int, danmaku_description: str):
            # 30% for setup, 65% for download, 5% for db write
            current_total_progress = 30 + (danmaku_progress / 100) * 65
            await progress_callback(current_total_progress, danmaku_description)

        all_comments_from_source = await scraper.get_comments(provider_episode_id, progress_callback=sub_progress_callback)

        if not all_comments_from_source:
            await crud.update_episode_fetch_time(pool, episode_id)
            raise TaskSuccess("未找到任何弹幕。")

        # 新增：在插入前，先筛选出数据库中不存在的新弹幕，以避免产生大量的“重复条目”警告。
        await progress_callback(95, "正在比对新旧弹幕...")
        existing_cids = await crud.get_existing_comment_cids(pool, episode_id)
        new_comments = [c for c in all_comments_from_source if str(c.get('cid')) not in existing_cids]

        if not new_comments:
            await crud.update_episode_fetch_time(pool, episode_id)
            raise TaskSuccess("刷新完成，没有新增弹幕。")

        await progress_callback(96, f"正在写入 {len(new_comments)} 条新弹幕...")
        added_count = await crud.bulk_insert_comments(pool, episode_id, new_comments)
        await crud.update_episode_fetch_time(pool, episode_id)
        logger.info(f"分集 ID: {episode_id} 刷新完成，新增 {added_count} 条弹幕。")
        raise TaskSuccess(f"刷新完成，新增 {added_count} 条弹幕。")
    except TaskSuccess:
        # 任务成功完成，直接重新抛出，由 TaskManager 处理
        raise
    except Exception as e:
        logger.error(f"刷新分集 ID: {episode_id} 时发生严重错误: {e}", exc_info=True)
        raise # Re-raise so the task manager catches it and marks as FAILED

async def reorder_episodes_task(source_id: int, pool: aiomysql.Pool, progress_callback: Callable):
    """后台任务：重新编号一个源的所有分集。"""
    logger.info(f"开始重整源 ID: {source_id} 的分集顺序。")
    await progress_callback(0, "正在获取分集列表...")
    
    try:
        # 获取所有分集，按现有顺序排序
        episodes = await crud.get_episodes_for_source(pool, source_id)
        if not episodes:
            raise TaskSuccess("没有找到分集，无需重整。")

        total_episodes = len(episodes)
        updated_count = 0
        
        # 开始事务
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await conn.begin()
                try:
                    for i, episode in enumerate(episodes):
                        new_index = i + 1
                        if episode['episode_index'] != new_index:
                            await cursor.execute("UPDATE episode SET episode_index = %s WHERE id = %s", (new_index, episode['id']))
                            updated_count += 1
                        await progress_callback(int(((i + 1) / total_episodes) * 100), f"正在处理分集 {i+1}/{total_episodes}...")
                    await conn.commit()
                except Exception as e:
                    await conn.rollback()
                    logger.error(f"重整源 ID {source_id} 时数据库事务失败: {e}", exc_info=True)
                    raise
        raise TaskSuccess(f"重整完成，共更新了 {updated_count} 个分集的集数。")
    except Exception as e:
        logger.error(f"重整分集任务 (源ID: {source_id}) 失败: {e}", exc_info=True)
        raise

class ManualImportRequest(models.BaseModel):
    title: str
    episode_index: int
    url: str

@router.post("/library/source/{source_id}/manual-import", status_code=status.HTTP_202_ACCEPTED, summary="手动导入单个分集弹幕")
async def manual_import_episode(
    source_id: int,
    request_data: ManualImportRequest,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，从给定的URL手动导入弹幕。"""
    source_info = await crud.get_anime_source_info(pool, source_id)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    provider_name = source_info['provider_name']
    
    url_prefixes = {
        'bilibili': 'bilibili.com', 'tencent': 'v.qq.com', 'iqiyi': 'iqiyi.com',
        'youku': 'youku.com', 'mgtv': 'mgtv.com', 'acfun': 'acfun.cn', 'renren': 'rrsp.com.cn'
    }
    expected_prefix = url_prefixes.get(provider_name)
    if not expected_prefix or expected_prefix not in request_data.url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"提供的URL与当前源 '{provider_name}' 不匹配。")

    task_title = f"手动导入: {request_data.title}"
    task_coro = lambda callback: manual_import_task(
        source_id=source_id, title=request_data.title, episode_index=request_data.episode_index,
        url=request_data.url, provider_name=provider_name,
        progress_callback=callback, pool=pool, manager=scraper_manager
    )
    task_id, _ = await task_manager.submit_task(task_coro, task_title)
    return {"message": f"手动导入任务 '{task_title}' 已提交。", "task_id": task_id}

async def manual_import_task(
    source_id: int, title: str, episode_index: int, url: str, provider_name: str,
    progress_callback: Callable, pool: aiomysql.Pool, manager: ScraperManager
):
    """后台任务：从URL手动导入弹幕。"""
    logger.info(f"开始手动导入任务: source_id={source_id}, title='{title}', url='{url}'")
    await progress_callback(10, "正在准备导入...")
    
    try:
        scraper = manager.get_scraper(provider_name)
        
        provider_episode_id = None
        if hasattr(scraper, 'get_ids_from_url'): provider_episode_id = await scraper.get_ids_from_url(url)
        elif hasattr(scraper, 'get_danmaku_id_from_url'): provider_episode_id = await scraper.get_danmaku_id_from_url(url)
        elif hasattr(scraper, 'get_tvid_from_url'): provider_episode_id = await scraper.get_tvid_from_url(url)
        elif hasattr(scraper, 'get_vid_from_url'): provider_episode_id = await scraper.get_vid_from_url(url)
        
        if not provider_episode_id: raise ValueError(f"无法从URL '{url}' 中解析出有效的视频ID。")

        # 修正：处理 Bilibili 和 MGTV 返回的字典ID，并将其格式化为 get_comments 期望的字符串格式。
        episode_id_for_comments = provider_episode_id
        if isinstance(provider_episode_id, dict):
            if provider_name == 'bilibili':
                episode_id_for_comments = f"{provider_episode_id.get('aid')},{provider_episode_id.get('cid')}"
            elif provider_name == 'mgtv':
                # MGTV 的 get_comments 期望 "cid,vid"
                episode_id_for_comments = f"{provider_episode_id.get('cid')},{provider_episode_id.get('vid')}"
            else:
                # 对于其他可能的字典返回，将其字符串化
                episode_id_for_comments = str(provider_episode_id)

        await progress_callback(20, f"已解析视频ID: {episode_id_for_comments}")
        comments = await scraper.get_comments(episode_id_for_comments, progress_callback=progress_callback)
        if not comments: raise TaskSuccess("未找到任何弹幕。")

        await progress_callback(90, "正在写入数据库...")
        episode_db_id = await crud.get_or_create_episode(pool, source_id, episode_index, title, url, episode_id_for_comments)
        added_count = await crud.bulk_insert_comments(pool, episode_db_id, comments)
        raise TaskSuccess(f"手动导入完成，新增 {added_count} 条弹幕。")
    except TaskSuccess:
        raise
    except Exception as e:
        logger.error(f"手动导入任务失败: {e}", exc_info=True)
        raise

@router.post("/import", status_code=status.HTTP_202_ACCEPTED, summary="从指定数据源导入弹幕")
async def import_from_provider(
    request_data: models.ImportRequest,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager)
):
    try:
        # 在启动任务前检查provider是否存在
        scraper_manager.get_scraper(request_data.provider)
        logger.info(f"用户 '{current_user.username}' 正在从 '{request_data.provider}' 导入 '{request_data.anime_title}' (media_id={request_data.media_id})")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # 只有在全量导入（非单集导入）时才执行此检查
    if request_data.current_episode_index is None:
        source_exists = await crud.check_source_exists_by_media_id(pool, request_data.provider, request_data.media_id)
        if source_exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该数据源已存在于弹幕库中，无需重复导入。"
            )

    # 创建一个将传递给任务管理器的协程工厂 (lambda)
    task_coro = lambda callback: generic_import_task(
        provider=request_data.provider,
        media_id=request_data.media_id,
        anime_title=request_data.anime_title,
        media_type=request_data.type,
        season=request_data.season,
        current_episode_index=request_data.current_episode_index,
        image_url=request_data.image_url,
        douban_id=request_data.douban_id,
        tmdb_id=request_data.tmdb_id,
        imdb_id=None, tvdb_id=None, # 手动导入时这些ID为空,
        task_manager=task_manager, # 传递 task_manager
        progress_callback=callback,
        pool=pool,
        manager=scraper_manager
    )
    
    # 构造任务标题
    task_title = f"导入: {request_data.anime_title}"
    # 如果是电视剧且指定了单集导入，则在标题中追加季和集信息
    if request_data.type == "tv_series" and request_data.current_episode_index is not None and request_data.season is not None:
        task_title += f" - S{request_data.season:02d}E{request_data.current_episode_index:02d}"

    # 提交任务并获取任务ID
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    return {"message": f"'{request_data.anime_title}' 的导入任务已提交。请在任务管理器中查看进度。", "task_id": task_id}

class EditedImportRequest(models.BaseModel):
    provider: str
    media_id: str = Field(..., alias="mediaId")
    anime_title: str
    media_type: str
    season: int
    image_url: Optional[str] = Field(None, alias="imageUrl")
    douban_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    # 关键区别：直接提供一个分集列表
    episodes: List[models.ProviderEpisodeInfo]

@router.post("/import/edited", status_code=status.HTTP_202_ACCEPTED, summary="导入编辑后的分集列表")
async def import_edited_episodes(
    request_data: EditedImportRequest,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，使用用户在前端编辑过的分集列表进行导入。"""
    task_title = f"编辑后导入: {request_data.anime_title}"
    task_coro = lambda callback: edited_import_task(
        request_data=request_data,
        progress_callback=callback,
        pool=pool,
        manager=scraper_manager
    )
    task_id, _ = await task_manager.submit_task(task_coro, task_title)
    return {"message": f"'{request_data.anime_title}' 的编辑导入任务已提交。", "task_id": task_id}

@router.post("/tasks/{task_id}/pause", status_code=status.HTTP_202_ACCEPTED, summary="暂停一个正在运行的任务")
async def pause_task(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """暂停一个指定的、当前正在运行的任务。"""
    success = await task_manager.pause_task(task_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="无法暂停任务，因为它不是当前正在运行的任务。")
    return {"message": "暂停请求已发送。"}

@router.post("/tasks/{task_id}/resume", status_code=status.HTTP_202_ACCEPTED, summary="恢复一个已暂停的任务")
async def resume_task(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """恢复一个指定的、已暂停的任务。"""
    success = await task_manager.resume_task(task_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="无法恢复任务，因为它不是当前已暂停的任务。")
    return {"message": "恢复请求已发送。"}

@router.post("/tasks/{task_id}/abort", status_code=status.HTTP_202_ACCEPTED, summary="中止一个正在运行的任务")
async def abort_running_task(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """
    尝试中止一个任务。
    如果任务是当前正在运行的任务，会尝试取消它。
    如果任务不是当前任务（例如，卡在“运行中”状态），则会强制将其状态更新为“失败”。
    """
    task = await crud.get_task_from_history_by_id(pool, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if task['status'] != TaskStatus.RUNNING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"无法中止一个状态为 '{task['status']}' 的任务。")

    logger.info(f"用户 '{current_user.username}' 请求中止任务 ID: {task_id} ({task['title']})。")
    
    aborted = await task_manager.abort_current_task(task_id)
    if aborted:
        return {"message": "任务中止请求已发送。"}
    else:
        logger.warning(f"无法优雅地中止任务 {task_id}，将强制标记为失败。")
        await crud.finalize_task_in_history(pool, task_id, TaskStatus.FAILED, "被用户手动中止")
        return {"message": "任务已被强制标记为失败。"}

@auth_router.post("/token", response_model=models.Token, summary="用户登录获取令牌")
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    user = await crud.get_user_by_username(pool, form_data.username)
    if not user or not security.verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = await security.create_access_token(
        data={"sub": user["username"]}, pool=pool
    )
    # 更新用户的登录信息
    await crud.update_user_login_info(pool, user["username"], access_token)

    return {"access_token": access_token, "token_type": "bearer"}


@auth_router.get("/users/me", response_model=models.User, summary="获取当前用户信息")
async def read_users_me(current_user: models.User = Depends(security.get_current_user)):
    return current_user

@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="用户登出")
async def logout():
    """
    用户登出。前端应清除本地存储的token。
    """
    return

@router.get("/scheduled-tasks/available", response_model=List[Dict[str, str]], summary="获取所有可用的定时任务类型")
async def get_available_job_types(
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    """获取所有已成功加载的、可供用户选择的定时任务类型。"""
    return scheduler.get_available_jobs()


# --- Scheduled Tasks API ---

class ScheduledTaskCreate(models.BaseModel):
    name: str
    job_type: str
    cron_expression: str
    is_enabled: bool = True

class ScheduledTaskUpdate(models.BaseModel):
    name: str
    cron_expression: str
    is_enabled: bool

class ScheduledTaskInfo(ScheduledTaskCreate):
    id: str
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None

@router.get("/scheduled-tasks", response_model=List[ScheduledTaskInfo], summary="获取所有定时任务")
async def get_scheduled_tasks(
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    tasks = await scheduler.get_all_tasks()
    return [ScheduledTaskInfo.model_validate(t) for t in tasks]

@router.post("/scheduled-tasks", response_model=ScheduledTaskInfo, status_code=201, summary="创建定时任务")
async def create_scheduled_task(
    task_data: ScheduledTaskCreate,
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    try:
        new_task = await scheduler.add_task(task_data.name, task_data.job_type, task_data.cron_expression, task_data.is_enabled)
        return ScheduledTaskInfo.model_validate(new_task)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"创建定时任务失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="创建定时任务时发生内部错误")

class ImportFromUrlRequest(models.BaseModel):
    provider: str
    url: str
    title: str
    media_type: str
    season: int

@router.post("/import-from-url", status_code=status.HTTP_202_ACCEPTED, summary="从URL导入弹幕")
async def import_from_url(
    request_data: ImportFromUrlRequest,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager)
):
    provider = request_data.provider
    url = request_data.url
    title = request_data.title
    
    try:
        scraper = scraper_manager.get_scraper(provider)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    media_id_for_scraper = None

    try:
        if provider == 'bilibili':
            bvid_match = re.search(r'video/(BV[a-zA-Z0-9]+)', url)
            ssid_match = re.search(r'bangumi/play/ss(\d+)', url)
            epid_match = re.search(r'bangumi/play/ep(\d+)', url)
            if ssid_match:
                media_id_for_scraper = f"ss{ssid_match.group(1)}"
            elif bvid_match:
                media_id_for_scraper = f"bv{bvid_match.group(1)}"
            elif epid_match:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
                    resp.raise_for_status()
                    html_text = resp.text
                    ssid_match_from_page = re.search(r'"season_id":(\d+)', html_text)
                    if ssid_match_from_page:
                        media_id_for_scraper = f"ss{ssid_match_from_page.group(1)}"
        elif provider == 'tencent':
            cid_match = re.search(r'/cover/([^/]+)', url)
            if cid_match:
                media_id_for_scraper = cid_match.group(1)
        elif provider == 'iqiyi':
            linkid_match = re.search(r'v_(\w+)\.html', url)
            if linkid_match:
                media_id_for_scraper = linkid_match.group(1)
        elif provider == 'youku':
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
                resp.raise_for_status()
                html_text = resp.text
                showid_match = re.search(r'showid:"(\d+)"', html_text)
                if showid_match:
                    media_id_for_scraper = showid_match.group(1)
        elif provider == 'mgtv':
            cid_match = re.search(r'/b/(\d+)/', url)
            if cid_match:
                media_id_for_scraper = cid_match.group(1)
    except Exception as e:
        logger.error(f"从URL解析媒体ID时出错: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="从URL解析媒体ID时出错")

    if not media_id_for_scraper:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"无法从URL '{url}' 中为提供商 '{provider}' 解析出媒体ID。")

    task_coro = lambda callback: generic_import_task(
        provider=provider, media_id=media_id_for_scraper, anime_title=title,
        media_type=request_data.media_type, season=request_data.season,
        current_episode_index=None, image_url=None, douban_id=None, tmdb_id=None, imdb_id=None, tvdb_id=None,
        progress_callback=callback, pool=pool, manager=scraper_manager, task_manager=task_manager
    )
    
    task_title = f"URL导入: {title}"
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    return {"message": f"'{title}' 的URL导入任务已提交。", "task_id": task_id}

@router.put("/scheduled-tasks/{task_id}", response_model=ScheduledTaskInfo, summary="更新定时任务")
async def update_scheduled_task(
    task_id: str,
    task_data: ScheduledTaskUpdate,
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    updated_task = await scheduler.update_task(task_id, task_data.name, task_data.cron_expression, task_data.is_enabled)
    if not updated_task:
        raise HTTPException(status_code=404, detail="找不到指定的任务ID")
    return ScheduledTaskInfo.model_validate(updated_task)

@router.delete("/scheduled-tasks/{task_id}", status_code=204, summary="删除定时任务")
async def delete_scheduled_task(task_id: str, current_user: models.User = Depends(security.get_current_user), scheduler: SchedulerManager = Depends(get_scheduler_manager)):
    await scheduler.delete_task(task_id)

@router.post("/scheduled-tasks/{task_id}/run", status_code=202, summary="立即运行一次定时任务")
async def run_scheduled_task_now(task_id: str, current_user: models.User = Depends(security.get_current_user), scheduler: SchedulerManager = Depends(get_scheduler_manager)):
    try:
        await scheduler.run_task_now(task_id)
        return {"message": "任务已触发运行"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@auth_router.put("/users/me/password", status_code=status.HTTP_204_NO_CONTENT, summary="修改当前用户密码")
async def change_current_user_password(
    password_data: models.PasswordChange,
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool)
):
    # 1. 从数据库获取完整的用户信息，包括哈希密码
    user_in_db = await crud.get_user_by_username(pool, current_user.username)
    if not user_in_db:
        # 理论上不会发生，因为 get_current_user 已经验证过
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 2. 验证旧密码是否正确
    if not security.verify_password(password_data.old_password, user_in_db["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect old password")

    # 3. 更新密码
    new_hashed_password = security.get_password_hash(password_data.new_password)
    await crud.update_user_password(pool, current_user.username, new_hashed_password)
