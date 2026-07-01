"""
匹配过程调试 API
用于排查"为什么匹配错 / 为什么没匹配上"，展示匹配链路每一步的中间结果。
"""
import asyncio
import logging
import time
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src import security
from src.db import crud, models, get_db_session, ConfigManager
from src.services import (
    ScraperManager, MetadataSourceManager, TitleRecognitionManager,
    convert_to_chinese_title,
)
from src.utils import parse_search_keyword
from src.ai.ai_matcher_manager import AIMatcherManager
from src.api.dependencies import (
    get_scraper_manager, get_metadata_manager, get_config_manager,
    get_title_recognition_manager, get_ai_matcher_manager,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/debug", tags=["调试工具"])


class MatchTraceRequest(BaseModel):
    title: str = Field(..., description="原始标题")
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    media_type: Optional[str] = None


class TraceStep(BaseModel):
    name: str
    duration_ms: float = 0
    success: bool = True
    input_data: Optional[Any] = None
    output_data: Optional[Any] = None
    details: Optional[str] = None


class MatchTraceResponse(BaseModel):
    title: str
    steps: List[TraceStep] = []
    total_duration_ms: float = 0
    result_count: int = 0


@router.post("/match-trace", summary="匹配过程调试")
async def match_trace(
    request: MatchTraceRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    title_recognition_manager: TitleRecognitionManager = Depends(get_title_recognition_manager),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
):
    """
    执行匹配调试：输入标题等信息，逐步展示匹配链路每一步的结果。
    不会实际导入数据，只返回中间过程和最终候选结果。
    """
    total_start = time.perf_counter()
    steps: List[TraceStep] = []
    keyword = request.title.strip()

    # 步骤1: 关键词解析
    t0 = time.perf_counter()
    try:
        parsed = parse_search_keyword(keyword)
        steps.append(TraceStep(
            name="关键词解析",
            duration_ms=(time.perf_counter() - t0) * 1000,
            input_data={"keyword": keyword},
            output_data=parsed,
        ))
    except Exception as e:
        steps.append(TraceStep(
            name="关键词解析", success=False,
            duration_ms=(time.perf_counter() - t0) * 1000,
            details=str(e),
        ))
        return MatchTraceResponse(title=keyword, steps=steps,
                                   total_duration_ms=(time.perf_counter() - total_start) * 1000)

    original_title = parsed.get("title", keyword)
    season_filter = request.season or parsed.get("season")
    episode_filter = request.episode or parsed.get("episode")

    # 步骤2: 自定义识别词 - 搜索预处理
    t0 = time.perf_counter()
    processed_title = original_title
    preprocessing_applied = False
    try:
        if title_recognition_manager:
            result = await title_recognition_manager.apply_search_preprocessing(
                original_title, episode_filter, season_filter
            )
            processed_title, processed_ep, processed_season, preprocessing_applied = result
            if preprocessing_applied:
                episode_filter = processed_ep if processed_ep != episode_filter else episode_filter
                season_filter = processed_season if processed_season != season_filter else season_filter
        steps.append(TraceStep(
            name="识别词预处理",
            duration_ms=(time.perf_counter() - t0) * 1000,
            input_data={"title": original_title},
            output_data={
                "processed_title": processed_title,
                "applied": preprocessing_applied,
                "season": season_filter, "episode": episode_filter,
            },
        ))
    except Exception as e:
        steps.append(TraceStep(
            name="识别词预处理", success=False,
            duration_ms=(time.perf_counter() - t0) * 1000,
            details=str(e),
        ))

    # 步骤3: 名称转换（非中文 -> 中文）
    t0 = time.perf_counter()
    converted_title = processed_title
    try:
        converted_title, conversion_applied = await convert_to_chinese_title(
            processed_title, config_manager, metadata_manager,
            ai_matcher_manager, current_user
        )
        steps.append(TraceStep(
            name="名称转换",
            duration_ms=(time.perf_counter() - t0) * 1000,
            input_data={"title": processed_title},
            output_data={"converted": converted_title, "applied": conversion_applied},
        ))
    except Exception as e:
        steps.append(TraceStep(
            name="名称转换", success=False,
            duration_ms=(time.perf_counter() - t0) * 1000,
            details=str(e),
        ))

    search_title = converted_title

    # 步骤4: 弹幕源搜索
    t0 = time.perf_counter()
    search_results = []
    source_timings = []
    try:
        search_results = await scraper_manager.search_all(
            [search_title], max_results_per_source=10
        )
        # 收集各源耗时
        for name, dur, cnt in scraper_manager.last_search_timing:
            source_timings.append({"source": name, "duration_ms": round(dur, 1), "results": cnt})
        steps.append(TraceStep(
            name="弹幕源搜索",
            duration_ms=(time.perf_counter() - t0) * 1000,
            input_data={"keywords": [search_title]},
            output_data={
                "total_results": len(search_results),
                "source_timings": source_timings,
                "results": [
                    {"title": r.title, "provider": r.provider, "mediaId": r.mediaId,
                     "type": r.type, "year": r.year}
                    for r in search_results[:20]
                ],
            },
        ))
    except Exception as e:
        steps.append(TraceStep(
            name="弹幕源搜索", success=False,
            duration_ms=(time.perf_counter() - t0) * 1000,
            details=str(e),
        ))

    # 步骤5: 辅助源搜索（别名扩展）
    t0 = time.perf_counter()
    try:
        has_aux = any(
            metadata_manager.is_source_enabled(src_name)
            for src_name in ['bangumi', 'tmdb', 'douban']
            if hasattr(metadata_manager, 'is_source_enabled')
        ) if hasattr(metadata_manager, 'is_source_enabled') else False

        if has_aux:
            user_obj = models.User(id=0, username="debug")
            aliases, supp_results, aux_map = await metadata_manager.search_supplemental_sources(
                search_title, user_obj
            )
            steps.append(TraceStep(
                name="辅助源搜索(别名)",
                duration_ms=(time.perf_counter() - t0) * 1000,
                input_data={"title": search_title},
                output_data={
                    "aliases_found": len(aliases),
                    "aliases": list(aliases)[:20],
                    "supplemental_count": len(supp_results),
                },
            ))
        else:
            steps.append(TraceStep(
                name="辅助源搜索(别名)",
                duration_ms=(time.perf_counter() - t0) * 1000,
                details="无已启用的辅助源",
                output_data={"aliases_found": 0, "aliases": []},
            ))
    except Exception as e:
        steps.append(TraceStep(
            name="辅助源搜索(别名)", success=False,
            duration_ms=(time.perf_counter() - t0) * 1000,
            details=str(e),
        ))

    total_ms = (time.perf_counter() - total_start) * 1000
    return MatchTraceResponse(
        title=keyword,
        steps=steps,
        total_duration_ms=round(total_ms, 1),
        result_count=len(search_results),
    )
