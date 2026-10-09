"""
Search相关的API端点
"""
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.schemas.auth import User
from src.schemas.search import AnimeInfo, AnimeSearchResponse
from src.services.scraper_manager import ScraperManager
from src.services.metadata_service import MetadataService
from src.workflows.title_recognition import TitleRecognitionWorkflow
from src.services.ai_service import AIService
from src.services.config_service import ConfigService
from src.utils.auth import security
from src.utils.parsing.episode_filter import parse_single_episode_filter_rules, apply_single_episode_filter
from src.workflows.search.entry_flow import search_home
from src.workflows.search.ui_storage import search_local_anime

from src.api.dependencies import (
    get_scraper_manager, get_metadata_service, get_config_service,
    get_title_recognition_manager, get_ai_service
)
from src.schemas.ui_models import UIProviderSearchResponse

from src.workflows.supplement_episodes import get_episodes_routed

logger = logging.getLogger(__name__)

router = APIRouter()






@router.get(
    "/search/anime",
    response_model=AnimeSearchResponse,
    summary="搜索本地数据库中的节目信息",
)
async def search_anime_local(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
) -> AnimeSearchResponse:
    """通过数据库服务搜索本地作品，不持有请求级会话。"""
    db_results = await search_local_anime(keyword)
    animes = [
        AnimeInfo(animeId=item["id"], animeTitle=item["title"], type=item["type"])
        for item in db_results
    ]
    return AnimeSearchResponse(animes=animes)

@router.get("/search/provider", response_model=UIProviderSearchResponse, summary="从外部数据源搜索节目")
async def search_anime_provider(
    request: Request,
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    pageSize: int = Query(10, ge=10, le=100, description="每页数量，10-100"),
    typeFilter: Optional[str] = Query(None, description="类型过滤: tv_series, movie"),
    yearFilter: Optional[int] = Query(None, description="年份过滤"),
    providerFilter: Optional[str] = Query(None, description="来源过滤: bilibili, tencent等"),
    titleFilter: Optional[str] = Query(None, description="标题关键词过滤"),
    manager: ScraperManager = Depends(get_scraper_manager),
    current_user: User = Depends(security.get_current_user),
    metadata_manager: MetadataService = Depends(get_metadata_service),
    title_recognition_manager: TitleRecognitionWorkflow = Depends(get_title_recognition_manager),
    config_service: ConfigService = Depends(get_config_service),
    ai_service: AIService = Depends(get_ai_service)
):
    """
    从所有已配置的数据源（如腾讯、B站等）搜索节目信息。
    此接口实现了智能的按季缓存机制，并保留了原有的别名搜索、过滤和排序逻辑。
    """
    # API 只负责 HTTP 契约，搜索策略由入口编排组合共用能力。
    try:
        payload = await search_home(
            keyword, scraper_manager=manager, metadata_manager=metadata_manager,
            ai_service=ai_service, title_recognition_manager=title_recognition_manager,
            config_service=config_service, user=current_user, page=page, page_size=pageSize,
            type_filter=typeFilter, year_filter=yearFilter,
            provider_filter=providerFilter, title_filter=titleFilter,
        )
        return UIProviderSearchResponse(**payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.RequestError as exc:
        logger.error("主页搜索发生网络错误: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=f"搜索 '{keyword}' 时发生网络错误: {exc}") from exc


@router.get("/search/episodes", response_model=Dict[str, Any], summary="获取搜索结果的分集列表")
async def get_episodes_for_search_result(
    provider: str = Query(...),
    media_id: str = Query(...),
    media_type: Optional[str] = Query(None), # Pass media_type to help scraper
    title: Optional[str] = Query(None, description="搜索结果标题，用于匹配单剧过滤规则"),
    manager: ScraperManager = Depends(get_scraper_manager),
    config_service: ConfigService = Depends(get_config_service),
    title_recognition_manager: TitleRecognitionWorkflow = Depends(get_title_recognition_manager),
    current_user: User = Depends(security.get_current_user)
):
    """为指定的搜索结果获取完整的分集列表。自动识别补充源mediaId并路由。"""
    try:
        episodes, excluded = await get_episodes_routed(manager,
            provider, media_id, db_media_type=media_type, return_filtered=True
        )
        # 单剧过滤（依赖作品标题，未下沉到 get_episodes_routed）
        filter_content = await config_service.get("singleEpisodeFilterRules", "")
        filter_rules = parse_single_episode_filter_rules(filter_content)
        # 适配识别词：title 为源站原名，过滤规则可能按识别词转换后的"入库名"配置。
        # 用 apply_storage_postprocessing 正向转换出入库名作为额外匹配候选。
        extra_filter_titles = []
        if title_recognition_manager and title:
            try:
                converted_title, _, was_converted, _, _ = await title_recognition_manager.apply_storage_postprocessing(
                    title, None, provider
                )
                if was_converted and converted_title and converted_title != title:
                    extra_filter_titles.append(converted_title)
            except Exception as e:
                logger.warning(f"单剧过滤识别词转换失败，仅用原名匹配: {e}")
        episodes, single_excluded = apply_single_episode_filter(
            episodes, filter_rules, title, provider, media_id,
            return_filtered=True, extra_titles=extra_filter_titles
        )
        excluded.extend(single_excluded)
        # 注：兜底全局分集标题过滤已统一收口到 manager.get_episodes_routed 内部，此处无需重复处理
        return {
            "episodes": episodes,
            "excludedEpisodes": [
                {**episode.model_dump(), "filterReason": reason}
                for episode, reason in excluded
            ],
        }
    except httpx.RequestError as e:
        # 新增：捕获网络错误
        error_message = f"从 {provider} 获取分集列表时发生网络错误: {e}"
        logger.error(f"获取分集列表失败 (provider={provider}, media_id={media_id}): {error_message}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error_message)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"获取分集列表失败 (provider={provider}, media_id={media_id}): {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取分集列表失败。")
