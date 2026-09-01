"""
御坂助手 · 写类工具（P3）
------------------------------------------------------------
写类工具权限为 WRITE，风险确认由 agent 在对话中自然完成（先说明再执行），
不依赖独立的确认事件与挂起状态机。
执行时从 context 取所需管理器（app.state 注入）。

首个写工具：刷新分集弹幕（提交后台任务，不直接改库，相对安全）。
"""

import hashlib
import logging
from typing import Any, Dict

from src.db import crud, models
from src import tasks
from ..security_gateway import ToolPermission
from .base import Tool, registry
from .search_session import get_result_item

logger = logging.getLogger(__name__)


async def _refresh_episode(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """为指定分集提交"刷新弹幕"后台任务。"""
    episode_id = arguments.get("episodeId")
    if not episode_id:
        return {"error": "缺少 episodeId"}

    session_factory = context.get("session_factory")
    task_manager = context.get("task_manager")
    scraper_manager = context.get("scraper_manager")
    rate_limiter = context.get("rate_limiter")
    config_manager = context.get("config_manager")
    if not all([session_factory, task_manager, scraper_manager, rate_limiter, config_manager]):
        return {"error": "运行环境不完整，无法提交任务"}

    async with session_factory() as session:
        info = await crud.get_episode_for_refresh(session, int(episode_id))
    if not info:
        return {"error": "分集不存在"}

    unique_key = f"refresh-episode-{episode_id}"
    task_id, _ = await task_manager.submit_task(
        lambda s, cb: tasks.refresh_episode_task(
            int(episode_id), s, scraper_manager, rate_limiter, cb, config_manager
        ),
        f"御坂助手刷新分集: {info['title']} [{info.get('providerName', '?')}]",
        unique_key=unique_key,
        task_type="refresh_episode",
        task_parameters={"episodeId": int(episode_id)},
    )
    return {"ok": True, "taskId": task_id, "message": "刷新分集任务已提交"}


async def _delete_anime(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """删除整个作品（含所有数据源、分集、弹幕）—— 高危不可逆，提交后台任务。"""
    anime_id = arguments.get("animeId")
    if not anime_id:
        return {"error": "缺少 animeId"}
    session_factory = context.get("session_factory")
    task_manager = context.get("task_manager")
    if not all([session_factory, task_manager]):
        return {"error": "运行环境不完整，无法提交任务"}
    async with session_factory() as session:
        detail = await crud.get_anime_full_details(session, int(anime_id))
    if not detail:
        return {"error": "作品不存在"}
    unique_key = f"delete-anime-{anime_id}"
    task_id, _ = await task_manager.submit_task(
        lambda s, cb: tasks.delete_anime_task(int(anime_id), s, cb),
        f"御坂助手删除作品: {detail.get('title')}",
        unique_key=unique_key, run_immediately=True,
    )
    return {"ok": True, "taskId": task_id, "message": f"删除作品「{detail.get('title')}」任务已提交"}


async def _delete_source(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """删除某数据源（含其分集与弹幕）—— 提交后台任务。"""
    source_id = arguments.get("sourceId")
    if not source_id:
        return {"error": "缺少 sourceId"}
    session_factory = context.get("session_factory")
    task_manager = context.get("task_manager")
    if not all([session_factory, task_manager]):
        return {"error": "运行环境不完整，无法提交任务"}
    unique_key = f"delete-source-{source_id}"
    task_id, _ = await task_manager.submit_task(
        lambda s, cb: tasks.delete_source_task(int(source_id), s, cb),
        f"御坂助手删除数据源 (sourceId={source_id})",
        unique_key=unique_key, run_immediately=True,
    )
    return {"ok": True, "taskId": task_id, "message": "删除数据源任务已提交"}


async def _run_scheduled_task(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """立即运行一个定时任务（按 taskId）。"""
    task_id = (arguments.get("taskId") or "").strip()
    if not task_id:
        return {"error": "缺少 taskId"}
    scheduler = context.get("scheduler_manager")
    if not scheduler:
        return {"error": "定时任务调度器不可用"}
    try:
        await scheduler.run_task_now(task_id)
    except Exception as e:  # noqa: BLE001
        return {"error": f"运行定时任务失败: {e}"}
    return {"ok": True, "message": "定时任务已触发立即运行"}


async def _import_selected(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """直接导入某个搜索候选源（三段式导入·直接导入）。

    用 search_media 返回的 (searchId, resultIndex) 定位候选，
    episode 为空导入整季/整部；指定 episode（如 '5'）则只导入该单集。
    复用 generic_import_task，与 control API /import/direct 行为一致。
    """
    search_id = (arguments.get("searchId") or "").strip()
    result_index = arguments.get("resultIndex")
    episode = arguments.get("episode")

    session_factory = context.get("session_factory")
    config_manager = context.get("config_manager")
    scraper_manager = context.get("scraper_manager")
    metadata_manager = context.get("metadata_manager")
    task_manager = context.get("task_manager")
    rate_limiter = context.get("rate_limiter")
    title_recognition_manager = context.get("title_recognition_manager")
    if not all([session_factory, config_manager, scraper_manager, metadata_manager, task_manager]):
        return {"error": "运行环境不完整，无法提交导入任务"}

    async with session_factory() as session:
        item, err = await get_result_item(session, search_id, result_index)
        if err:
            return {"error": err}

        # 指定单集时设置 currentEpisodeIndex，让 generic_import_task 走单集导入
        current_ep = None
        if episode is not None:
            try:
                current_ep = int(episode)
            except (ValueError, TypeError):
                return {"error": f"episode 必须是整数集号，收到: {episode!r}"}

        # 提交前做与 control API 一致的重复检查
        duplicate_reason = await crud.check_duplicate_import(
            session=session, provider=item.provider, media_id=item.mediaId,
            anime_title=item.title, media_type=item.type, season=item.season,
            year=item.year, is_single_episode=current_ep is not None,
            episode_index=current_ep, title_recognition_manager=title_recognition_manager,
        )
    if duplicate_reason:
        return {"error": f"重复导入：{duplicate_reason}"}

    title_parts = [f"御坂助手导入: {item.title} ({item.provider})"]
    if current_ep is not None and item.season is not None:
        title_parts.append(f"S{item.season:02d}E{current_ep:02d}")
    task_title = " ".join(title_parts)
    unique_key = f"import-{item.provider}-{item.mediaId}"
    if current_ep is not None:
        unique_key += f"-ep{current_ep}"

    task_parameters = {
        "provider": item.provider, "mediaId": item.mediaId, "animeTitle": item.title,
        "mediaType": item.type, "season": item.season, "episode": current_ep,
        "year": item.year, "imageUrl": item.imageUrl,
    }
    task_id, _ = await task_manager.submit_task(
        lambda s, cb: tasks.generic_import_task(
            provider=item.provider, mediaId=item.mediaId, animeTitle=item.title,
            mediaType=item.type, season=item.season, year=item.year,
            currentEpisodeIndex=current_ep, imageUrl=item.imageUrl,
            config_manager=config_manager, metadata_manager=metadata_manager,
            progress_callback=cb, session=s, manager=scraper_manager,
            task_manager=task_manager, rate_limiter=rate_limiter,
            title_recognition_manager=title_recognition_manager,
        ),
        task_title, unique_key=unique_key, task_parameters=task_parameters,
    )
    return {"ok": True, "taskId": task_id, "message": f"「{item.title}」({item.provider}) 导入任务已提交"}


async def _import_edited(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """编辑后导入：只导入指定的若干分集（三段式导入·编辑导入）。

    用 (searchId, resultIndex) 定位候选，episodes 为要导入的分集序号列表
    （如 [1,3,5]）；工具会先取该源完整分集列表，按序号筛出对应分集再提交。
    复用 edited_import_task，与 control API /import/edited 行为一致。
    """
    search_id = (arguments.get("searchId") or "").strip()
    result_index = arguments.get("resultIndex")
    episode_indexes = arguments.get("episodeIndexes")
    if not isinstance(episode_indexes, list) or not episode_indexes:
        return {"error": "缺少 episodeIndexes（要导入的分集序号列表，如 [1,3,5]）"}
    try:
        wanted = {int(x) for x in episode_indexes}
    except (ValueError, TypeError):
        return {"error": "episodeIndexes 必须是整数集号列表"}

    session_factory = context.get("session_factory")
    config_manager = context.get("config_manager")
    scraper_manager = context.get("scraper_manager")
    metadata_manager = context.get("metadata_manager")
    task_manager = context.get("task_manager")
    rate_limiter = context.get("rate_limiter")
    title_recognition_manager = context.get("title_recognition_manager")
    if not all([session_factory, config_manager, scraper_manager, metadata_manager, task_manager]):
        return {"error": "运行环境不完整，无法提交导入任务"}

    async with session_factory() as session:
        item, err = await get_result_item(session, search_id, result_index)
    if err:
        return {"error": err}

    # 取该源完整分集，按 episodeIndex 筛出用户要的分集
    all_episodes = await scraper_manager.get_episodes_routed(
        item.provider, item.mediaId, db_media_type=item.type,
    )
    selected = [e for e in (all_episodes or []) if e.episodeIndex in wanted]
    if not selected:
        return {"error": f"在该源分集中未找到指定的集号 {sorted(wanted)}，请先用 get_provider_episodes 核对。"}

    edited_request = models.EditedImportRequest(
        provider=item.provider, mediaId=item.mediaId, animeTitle=item.title,
        mediaType=item.type, season=item.season, year=item.year,
        imageUrl=item.imageUrl, episodes=selected,
    )
    indexes_sorted = sorted(e.episodeIndex for e in selected)
    title_parts = [f"御坂助手编辑导入: {item.title} ({item.provider})"]
    if item.season is not None:
        title_parts.append(f"S{item.season:02d}")
    title_parts.append(f"E{indexes_sorted[0]:02d}" if len(indexes_sorted) == 1 else f"({len(indexes_sorted)}集)")
    task_title = " ".join(title_parts)

    episodes_hash = hashlib.md5(
        ",".join(map(str, indexes_sorted)).encode("utf-8")
    ).hexdigest()[:8]
    unique_key = f"import-{item.provider}-{item.mediaId}-{episodes_hash}"

    task_parameters = {
        "animeTitle": item.title, "season": item.season,
        "episode": indexes_sorted[0], "episodeCount": len(selected),
        "provider": item.provider, "mediaId": item.mediaId,
        "type": item.type, "mediaType": item.type, "imageUrl": item.imageUrl or "",
    }
    task_id, _ = await task_manager.submit_task(
        lambda s, cb: tasks.edited_import_task(
            request_data=edited_request, progress_callback=cb, session=s,
            config_manager=config_manager, manager=scraper_manager,
            rate_limiter=rate_limiter, metadata_manager=metadata_manager,
            title_recognition_manager=title_recognition_manager,
        ),
        task_title, unique_key=unique_key, task_parameters=task_parameters,
    )
    return {"ok": True, "taskId": task_id,
            "message": f"「{item.title}」({item.provider}) 共 {len(selected)} 集的编辑导入任务已提交"}


def register_write_tools() -> None:
    """注册写类工具（权限 WRITE，需二次确认）。"""
    registry.register(Tool(
        name="refresh_episode_danmaku",
        description="为指定分集重新从源站抓取最新弹幕（提交后台任务）。需要用户确认后才会执行。",
        parameters={
            "type": "object",
            "properties": {
                "episodeId": {"type": "integer", "description": "要刷新的分集 ID"},
            },
            "required": ["episodeId"],
        },
        permission=ToolPermission.WRITE,
        executor=_refresh_episode,
        running_label="刷新分集弹幕",
    ))
    registry.register(Tool(
        name="delete_anime",
        description="删除整个作品，含其所有数据源、分集与弹幕。此操作不可逆！需用户确认后才执行。先用 search_library 确认 animeId。",
        parameters={
            "type": "object",
            "properties": {
                "animeId": {"type": "integer", "description": "要删除的作品 ID"},
            },
            "required": ["animeId"],
        },
        permission=ToolPermission.WRITE,
        executor=_delete_anime,
        running_label="删除作品",
    ))
    registry.register(Tool(
        name="delete_source",
        description="删除某作品下的一个数据源，含其分集与弹幕。不可逆！需用户确认。先用 get_anime_sources 确认 sourceId。",
        parameters={
            "type": "object",
            "properties": {
                "sourceId": {"type": "integer", "description": "要删除的数据源 ID"},
            },
            "required": ["sourceId"],
        },
        permission=ToolPermission.WRITE,
        executor=_delete_source,
        running_label="删除数据源",
    ))
    registry.register(Tool(
        name="run_scheduled_task",
        description="立即触发运行一个定时任务(如增量刷新)。需用户确认。taskId 来自定时任务列表。",
        parameters={
            "type": "object",
            "properties": {
                "taskId": {"type": "string", "description": "定时任务 ID"},
            },
            "required": ["taskId"],
        },
        permission=ToolPermission.WRITE,
        executor=_run_scheduled_task,
        running_label="运行定时任务",
    ))
    registry.register(Tool(
        name="import_selected",
        description=(
            "直接导入某个搜索候选源（三段式导入第二步）。用 search_media 返回的 "
            "searchId + resultIndex 定位候选，episode 为空导入整季，指定集号（如 '5'）则只导入该单集。"
            "这是「搜索→选→导入」的标准流程，必须先让用户从 search_media 的候选里选一个再导入。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "searchId": {"type": "string", "description": "search_media 返回的 searchId"},
                "resultIndex": {"type": "integer", "description": "候选结果索引（从 0 开始）"},
                "episode": {"type": "string", "description": "可选：集号，如 '5'；不填导入整季/整部"},
            },
            "required": ["searchId", "resultIndex"],
        },
        permission=ToolPermission.WRITE,
        executor=_import_selected,
        running_label="导入候选源",
    ))
    registry.register(Tool(
        name="import_edited",
        description=(
            "编辑后导入：只导入指定的若干分集（三段式导入·编辑导入）。用 search_media 的 "
            "searchId + resultIndex 定位候选，episodeIndexes 为要导入的集号列表（如 [1,3,5]）。"
            "适合用户只要某几集、或需要手动纳入被黑名单过滤掉的分集时使用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "searchId": {"type": "string", "description": "search_media 返回的 searchId"},
                "resultIndex": {"type": "integer", "description": "候选结果索引"},
                "episodeIndexes": {"type": "array", "items": {"type": "integer"},
                                    "description": "要导入的分集序号列表，如 [1,3,5,7,9]"},
            },
            "required": ["searchId", "resultIndex", "episodeIndexes"],
        },
        permission=ToolPermission.WRITE,
        executor=_import_edited,
        running_label="编辑导入分集",
    ))

