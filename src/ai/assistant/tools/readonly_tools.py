"""
御坂助手 · 首批只读工具（P2）
------------------------------------------------------------
均为 READ_ONLY：查询媒体库、查询任务列表、查询单个任务状态。
全部走现有 crud，只读不改数据，最安全。

context 约定：
- context["session_factory"]: async_sessionmaker，用于开 DB 会话
执行函数返回可 JSON 序列化的 dict，供回灌给模型。
"""

import logging
from typing import Any, Dict

from src.db import crud, models
# 直接从子模块导入，绕过 src.services.__init__（它会加载 notification_service，
# 进而 → llm_menu → src.ai.assistant，形成循环）。
# 这样依赖精确指向真正需要的 search 模块，不牵连整个 services 包。
from src.services.search import unified_search
from ..security_gateway import ToolPermission
from .base import Tool, registry
from .search_session import save_search_results, get_result_item

logger = logging.getLogger(__name__)

# 单次返回给模型的最大条数（控制 token）
_MAX_ITEMS = 15


async def _search_library(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """按关键词查询已收录的弹幕库作品。"""
    keyword = (arguments.get("keyword") or "").strip()
    session_factory = context.get("session_factory")
    if not session_factory:
        return {"error": "会话不可用"}
    async with session_factory() as session:
        result = await crud.get_library_anime(session, keyword=keyword or None)
    items = result.get("list", [])[:_MAX_ITEMS]
    simplified = [
        {
            "animeId": it.get("animeId"),
            "title": it.get("title"),
            "type": it.get("type"),
            "season": it.get("season"),
            "episodeCount": it.get("episodeCount"),
            "year": it.get("year"),
        }
        for it in items
    ]
    return {"total": result.get("total", len(simplified)), "items": simplified}


async def _list_tasks(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查询后台任务列表，可按状态过滤（all/in_progress/completed）。"""
    status = (arguments.get("status") or "all").strip()
    search = (arguments.get("search") or "").strip() or None
    if status not in ("all", "in_progress", "completed"):
        status = "all"
    session_factory = context.get("session_factory")
    if not session_factory:
        return {"error": "会话不可用"}
    async with session_factory() as session:
        result = await crud.get_tasks_from_history(
            session, search, status, "all", 1, _MAX_ITEMS
        )
    items = result.get("list", [])[:_MAX_ITEMS]
    simplified = [
        {
            "taskId": it.get("taskId"),
            "title": it.get("title"),
            "status": it.get("status"),
            "progress": it.get("progress"),
            "description": (it.get("description") or "")[:120],
        }
        for it in items
    ]
    return {"total": result.get("total", len(simplified)), "items": simplified}


async def _get_task_status(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查询单个任务的详细状态。"""
    task_id = (arguments.get("taskId") or "").strip()
    if not task_id:
        return {"error": "缺少 taskId"}
    session_factory = context.get("session_factory")
    if not session_factory:
        return {"error": "会话不可用"}
    async with session_factory() as session:
        detail = await crud.get_task_details_from_history(session, task_id)
    if not detail:
        return {"error": "任务不存在或已被清理"}
    return {
        "taskId": detail.get("taskId"),
        "title": detail.get("title"),
        "status": detail.get("status"),
        "progress": detail.get("progress"),
        "description": (detail.get("description") or "")[:300],
    }


async def _get_anime_sources(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查询某作品关联的所有弹幕数据源。"""
    anime_id = arguments.get("animeId")
    if not anime_id:
        return {"error": "缺少 animeId"}
    session_factory = context.get("session_factory")
    if not session_factory:
        return {"error": "会话不可用"}
    async with session_factory() as session:
        sources = await crud.get_anime_sources(session, int(anime_id))
    simplified = [
        {
            "sourceId": s.get("sourceId"),
            "providerName": s.get("providerName"),
            "episodeCount": s.get("episodeCount"),
            "isFavorited": s.get("isFavorited"),
        }
        for s in (sources or [])[:_MAX_ITEMS]
    ]
    return {"total": len(sources or []), "sources": simplified}


async def _get_source_episodes(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查询某数据源下的分集列表（集数、标题、弹幕数）。"""
    source_id = arguments.get("sourceId")
    if not source_id:
        return {"error": "缺少 sourceId"}
    session_factory = context.get("session_factory")
    if not session_factory:
        return {"error": "会话不可用"}
    async with session_factory() as session:
        result = await crud.get_episodes_for_source(session, int(source_id), 1, _MAX_ITEMS)
    items = result.get("list", [])[:_MAX_ITEMS]
    simplified = [
        {
            "episodeId": it.get("episodeId"),
            "title": it.get("title"),
            "episodeIndex": it.get("episodeIndex"),
            "commentCount": it.get("commentCount"),
        }
        for it in items
    ]
    return {"total": result.get("total", len(simplified)), "episodes": simplified}


async def _get_anime_detail(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查询单个作品的完整详情（元数据ID、别名、类型季度等）。"""
    anime_id = arguments.get("animeId")
    if not anime_id:
        return {"error": "缺少 animeId"}
    session_factory = context.get("session_factory")
    if not session_factory:
        return {"error": "会话不可用"}
    async with session_factory() as session:
        detail = await crud.get_anime_full_details(session, int(anime_id))
    if not detail:
        return {"error": "作品不存在"}
    # 只回灌关键字段，控制 token
    keys = ("title", "type", "season", "year", "episodeCount",
            "tmdbId", "bangumiId", "imdbId", "tvdbId", "doubanId")
    return {k: detail.get(k) for k in keys if detail.get(k) is not None}


async def _list_tokens(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查询对外弹幕 API 的 Token 列表（不含完整密钥，仅名称与状态）。"""
    session_factory = context.get("session_factory")
    if not session_factory:
        return {"error": "会话不可用"}
    async with session_factory() as session:
        tokens = await crud.get_all_api_tokens(session)
    simplified = [
        {
            "name": t.get("name"),
            "isEnabled": t.get("isEnabled"),
            "dailyCallCount": t.get("dailyCallCount"),
            "dailyCallLimit": t.get("dailyCallLimit"),
        }
        for t in (tokens or [])[:_MAX_ITEMS]
    ]
    return {"total": len(tokens or []), "tokens": simplified}


async def _search_media(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """全网弹幕源搜索（三段式导入第一步）。

    在所有启用的弹幕源搜索候选，结果存入搜索会话缓存并返回 searchId +
    带 resultIndex 的候选清单，供后续 get_provider_episodes / import_selected
    / import_edited 按 (searchId, resultIndex) 引用。
    """
    keyword = (arguments.get("keyword") or "").strip()
    if not keyword:
        return {"error": "缺少 keyword（要搜索的作品名）"}
    season = arguments.get("season")

    session_factory = context.get("session_factory")
    scraper_manager = context.get("scraper_manager")
    metadata_manager = context.get("metadata_manager")
    if not all([session_factory, scraper_manager]):
        return {"error": "运行环境不完整，无法执行搜索"}
    if not getattr(scraper_manager, "has_enabled_scrapers", False):
        return {"error": "没有启用的弹幕搜索源，请先在“搜索源”页面启用至少一个。"}

    async with session_factory() as session:
        results = await unified_search(
            search_term=keyword,
            session=session,
            scraper_manager=scraper_manager,
            metadata_manager=metadata_manager,
            use_alias_expansion=True,
            use_alias_filtering=True,
            use_title_filtering=True,
            use_source_priority_sorting=True,
        )
        # 若指定季度，仅保留电视剧且季度匹配的结果
        if season is not None:
            results = [
                r for r in results
                if getattr(r, "type", None) == "tv_series" and getattr(r, "season", None) == season
            ]
        search_id = await save_search_results(session, results)

    simplified = [
        {
            "resultIndex": i,
            "provider": r.provider,
            "title": r.title,
            "type": r.type,
            "season": r.season,
            "year": r.year,
            "episodeCount": r.episodeCount,
        }
        for i, r in enumerate(results[:_MAX_ITEMS])
    ]
    return {
        "searchId": search_id,
        "total": len(results),
        "results": simplified,
        "hint": "把 searchId 和某个 resultIndex 交给 import_selected 或 import_edited 完成导入；"
                "先让用户从上面的候选里选一个再导入，不要自作主张。",
    }


async def _get_provider_episodes(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查看某搜索候选源的分集列表（三段式导入可选中间步）。

    includeFiltered=1 时额外返回被黑名单/正则过滤掉的分集（如预告、花絮），
    供用户判断是否需要用 import_edited 手动纳入。默认 0 只看保留的分集。
    """
    search_id = (arguments.get("searchId") or "").strip()
    result_index = arguments.get("resultIndex")
    include_filtered = arguments.get("includeFiltered", 0) in (1, "1", True)

    session_factory = context.get("session_factory")
    scraper_manager = context.get("scraper_manager")
    if not all([session_factory, scraper_manager]):
        return {"error": "运行环境不完整，无法获取分集"}

    async with session_factory() as session:
        item, err = await get_result_item(session, search_id, result_index)
    if err:
        return {"error": err}

    result = await scraper_manager.get_episodes_routed(
        item.provider, item.mediaId, db_media_type=item.type,
        return_filtered=include_filtered,
    )
    if include_filtered:
        kept, filtered = result
    else:
        kept, filtered = result, []

    def _simplify(eps):
        return [
            {"episodeIndex": e.episodeIndex, "title": e.title, "episodeId": e.episodeId}
            for e in (eps or [])[:_MAX_ITEMS]
        ]

    resp = {
        "provider": item.provider,
        "title": item.title,
        "keptTotal": len(kept or []),
        "episodes": _simplify(kept),
    }
    if include_filtered:
        # 查看被过滤项：告知用户哪些分集被剔除，可用 import_edited 手动纳入
        resp["filteredTotal"] = len(filtered or [])
        resp["filteredEpisodes"] = _simplify(filtered)
    return resp


def register_readonly_tools() -> None:
    """注册首批只读工具到全局注册表。"""
    registry.register(Tool(
        name="search_library",
        description="在本地弹幕库中按关键词查询已收录的作品（电视剧/电影）。返回作品标题、类型、季度、集数等。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "作品名称关键词，留空则返回全部（截断）"},
            },
        },
        permission=ToolPermission.READ_ONLY,
        executor=_search_library,
        running_label="正在查询弹幕库",
    ))
    registry.register(Tool(
        name="list_tasks",
        description="查询后台任务列表（导入/刷新/删除等）。可按状态过滤：all 全部、in_progress 进行中、completed 已完成。",
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["all", "in_progress", "completed"],
                            "description": "任务状态过滤，默认 all"},
                "search": {"type": "string", "description": "按任务标题搜索关键词，可选"},
            },
        },
        permission=ToolPermission.READ_ONLY,
        executor=_list_tasks,
        running_label="正在查询任务列表",
    ))
    registry.register(Tool(
        name="get_task_status",
        description="根据 taskId 查询单个后台任务的详细状态与进度。",
        parameters={
            "type": "object",
            "properties": {
                "taskId": {"type": "string", "description": "任务 ID"},
            },
            "required": ["taskId"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_task_status,
        running_label="正在查询任务状态",
    ))
    registry.register(Tool(
        name="get_anime_sources",
        description="查询某作品(animeId)关联的所有弹幕数据源(如腾讯/B站)及各源的分集数。先用 search_library 拿到 animeId。",
        parameters={
            "type": "object",
            "properties": {
                "animeId": {"type": "integer", "description": "作品 ID（来自 search_library）"},
            },
            "required": ["animeId"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_anime_sources,
        running_label="正在查询数据源",
    ))
    registry.register(Tool(
        name="get_source_episodes",
        description="查询某数据源(sourceId)下的分集列表，含集数、标题、弹幕数量。先用 get_anime_sources 拿到 sourceId。",
        parameters={
            "type": "object",
            "properties": {
                "sourceId": {"type": "integer", "description": "数据源 ID（来自 get_anime_sources）"},
            },
            "required": ["sourceId"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_source_episodes,
        running_label="正在查询分集",
    ))
    registry.register(Tool(
        name="get_anime_detail",
        description="查询单个作品(animeId)的完整详情：类型、季度、年份、集数、以及 TMDB/Bangumi/IMDb 等元数据ID。",
        parameters={
            "type": "object",
            "properties": {
                "animeId": {"type": "integer", "description": "作品 ID（来自 search_library）"},
            },
            "required": ["animeId"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_anime_detail,
        running_label="正在查询作品详情",
    ))
    registry.register(Tool(
        name="list_tokens",
        description="查询对外提供弹幕 API 的 Token 列表（仅名称、启用状态、今日调用量，不含完整密钥）。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_list_tokens,
        running_label="正在查询 Token",
    ))
    registry.register(Tool(
        name="search_media",
        description=(
            "全网弹幕源搜索（三段式导入第一步）。按作品名在所有启用的弹幕源搜索候选，"
            "返回 searchId 和带 resultIndex 的候选清单。这是“帮我导入/下载《XX》弹幕”的入口："
            "先搜索列出候选，让用户选定某个 resultIndex 后，再调 import_selected（整季/单集）"
            "或 import_edited（挑指定分集）。严禁跳过用户选择自动导入。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "作品名称，如《爱情公寓》"},
                "season": {"type": "integer", "description": "季度号，可选；提供时只保留该季的电视剧结果"},
            },
            "required": ["keyword"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_search_media,
        running_label="正在全网搜索",
    ))
    registry.register(Tool(
        name="get_provider_episodes",
        description=(
            "查看某搜索候选源的分集列表（三段式导入可选中间步）。用 search_media 返回的 "
            "searchId + resultIndex 指定候选。includeFiltered=1 时额外返回被过滤掉的分集"
            "（预告/花絮等），供用户决定是否用 import_edited 手动纳入；默认 0 只看保留的分集。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "searchId": {"type": "string", "description": "search_media 返回的 searchId"},
                "resultIndex": {"type": "integer", "description": "候选结果索引"},
                "includeFiltered": {"type": "integer", "enum": [0, 1],
                                     "description": "是否查看被过滤项：0=否(默认)，1=返回被黑名单/正则过滤的分集"},
            },
            "required": ["searchId", "resultIndex"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_provider_episodes,
        running_label="正在获取分集列表",
    ))
