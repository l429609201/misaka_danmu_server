"""
御坂助手 · 写类工具（P3）
------------------------------------------------------------
写类工具权限为 WRITE；模型提出操作后由 Web 端展示确认卡，
服务端领取当前用户的一次性令牌后才执行。
确认后透传当前用户的 Bearer 凭据，复用固定 UI 路由的验证与任务提交逻辑。
"""

from typing import Any, Dict

from src.schemas.import_schemas import EditedImportRequest
from ..api_gateway.contracts import ActionEffect
from ..api_gateway.executor import ApiExecutionError, execute_operation
from ..api_gateway.policy import ApiOperation
from ..security_gateway import ToolPermission
from .base import Tool, registry
from .search_session import get_result_item


async def _call_ui(
    context: Dict[str, Any], method: str, path: str,
    *, path_params: Dict[str, Any] | None = None, body: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """确认后以真实用户身份调用固定 UI 路由，错误交给注册表包装。"""
    if context.get("confirmed_action") is not True:
        return {"error": "操作尚未获得本次确认"}
    authorization = context.get("authorization")
    if not isinstance(authorization, str) or not authorization.lower().startswith("bearer ") or not authorization[7:].strip():
        return {"error": "当前渠道没有可透传的登录凭据，无法调用站内接口"}
    try:
        result = await execute_operation(
            ApiOperation(operation_id="assistant.write", method=method, path=path,
                         summary="助手写操作", effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
                         path_params={key: key for key in (path_params or {})}),
            app=context.get("app"), authorization=authorization,
            path_params=path_params, body=body,
        )
    except ApiExecutionError as exc:
        return {"error": str(exc)}
    if not result.get("ok"):
        return {"error": result.get("error") or "接口调用失败"}
    data = result.get("data")
    return data if isinstance(data, dict) else {"data": data}


def _positive_id(arguments: Dict[str, Any], name: str) -> int | None:
    """只接受正整数主键，阻止布尔值和伪造路径。"""
    value = arguments.get(name)
    return value if type(value) is int and value > 0 else None


async def _refresh_episode(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """经 UI 路由刷新指定分集弹幕。"""
    episode_id = _positive_id(arguments, "episodeId")
    if episode_id is None:
        return {"error": "缺少或无效的 episodeId"}
    return await _call_ui(
        context, "POST", "/ui/library/episode/{episodeId}/refresh",
        path_params={"episodeId": episode_id},
    )


async def _delete_anime(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """经 UI 路由提交不可逆的作品删除任务。"""
    anime_id = _positive_id(arguments, "animeId")
    if anime_id is None:
        return {"error": "缺少或无效的 animeId"}
    return await _call_ui(
        context, "DELETE", "/ui/library/anime/{animeId}",
        path_params={"animeId": anime_id},
    )


async def _delete_source(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """经 UI 路由提交数据源删除任务。"""
    source_id = _positive_id(arguments, "sourceId")
    if source_id is None:
        return {"error": "缺少或无效的 sourceId"}
    return await _call_ui(
        context, "DELETE", "/ui/library/source/{sourceId}",
        path_params={"sourceId": source_id},
    )


async def _run_scheduled_task(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """经 UI 路由立即触发指定定时任务。"""
    raw_task_id = arguments.get("taskId")
    task_id = raw_task_id.strip() if isinstance(raw_task_id, str) else ""
    if not task_id:
        return {"error": "缺少 taskId"}
    return await _call_ui(
        context, "POST", "/ui/scheduled-tasks/{taskId}/run",
        path_params={"taskId": task_id},
    )


async def _import_selected(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """从搜索缓存选择候选，经 UI /import 路由提交直接导入。"""
    search_id = arguments.get("searchId")
    result_index = arguments.get("resultIndex")
    if not isinstance(search_id, str) or not search_id.strip() or type(result_index) is not int or result_index < 0:
        return {"error": "缺少或无效的 searchId / resultIndex"}
    item, err = await get_result_item(search_id.strip(), result_index)
    if err:
        return {"error": err}

    episode = arguments.get("episode")
    current_ep = None
    if episode is not None:
        if isinstance(episode, bool) or not isinstance(episode, (int, str)):
            return {"error": "episode 必须是正整数集号"}
        try:
            current_ep = int(episode)
        except ValueError:
            return {"error": "episode 必须是正整数集号"}
        if current_ep <= 0 or (isinstance(episode, str) and not episode.strip().isdecimal()):
            return {"error": "episode 必须是正整数集号"}

    # 候选字段来自缓存；调用参数中的 provider、mediaId 等伪造字段不参与请求。
    return await _call_ui(context, "POST", "/ui/import", body={
        "provider": item.provider, "mediaId": item.mediaId, "animeTitle": item.title,
        "type": item.type, "season": item.season, "year": item.year,
        "imageUrl": item.imageUrl, "currentEpisodeIndex": current_ep,
    })


async def _import_edited(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """缓存候选并按集号选择后，经 UI 路由提交编辑导入。"""
    search_id = arguments.get("searchId")
    result_index = arguments.get("resultIndex")
    if not isinstance(search_id, str) or not search_id.strip() or type(result_index) is not int or result_index < 0:
        return {"error": "缺少或无效的 searchId / resultIndex"}
    episode_indexes = arguments.get("episodeIndexes")
    if not isinstance(episode_indexes, list) or not episode_indexes:
        return {"error": "缺少 episodeIndexes（要导入的分集序号列表，如 [1,3,5]）"}
    if any(type(index) is not int or index <= 0 for index in episode_indexes):
        return {"error": "episodeIndexes 必须是正整数集号列表"}

    item, err = await get_result_item(search_id.strip(), result_index)
    if err:
        return {"error": err}
    scraper_manager = context.get("scraper_manager")
    if scraper_manager is None:
        return {"error": "运行环境不完整，无法获取分集"}

    # 保留缓存候选与分集交叉选择，不接受模型构造的任意 episode 数据。
    all_episodes = await scraper_manager.get_episodes_routed(
        item.provider, item.mediaId, db_media_type=item.type,
    )
    wanted = set(episode_indexes)
    selected = [ep for ep in (all_episodes or []) if ep.episodeIndex in wanted]
    if not selected or {ep.episodeIndex for ep in selected} != wanted:
        return {"error": f"在该源分集中未找到指定的集号 {sorted(wanted)}，请先用 get_provider_episodes 核对。"}

    edited_request = EditedImportRequest(
        provider=item.provider, mediaId=item.mediaId, animeTitle=item.title,
        mediaType=item.type, season=item.season, year=item.year,
        imageUrl=item.imageUrl, episodes=selected,
    )
    return await _call_ui(context, "POST", "/ui/import/edited", body=edited_request.model_dump())


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
        # 会向源站发起抓取请求，属外部副作用（旧弹幕会被新抓取结果覆盖）
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
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
        # 弹幕数据删除后无法找回，属不可逆操作
        effect=ActionEffect.DESTRUCTIVE_WRITE,
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
        # 同上：该源下的分集与弹幕一并丢失，不可恢复
        effect=ActionEffect.DESTRUCTIVE_WRITE,
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
        # 会真实触发抓取/刷新，对外部弹幕源产生请求，属外部副作用
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
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
        # 会向外部弹幕源发起抓取请求并写入数据，属外部副作用
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
    ))
    registry.register(Tool(
        name="import_edited",
        description=(
            "编辑后导入：只导入指定的若干分集（三段式导入·编辑导入）。用 search_media 的 "
            "searchId + resultIndex 定位候选，episodeIndexes 为要导入的集号列表（如 [1,3,5]）。"
            "适合用户只需要候选源中当前可选的若干集；被过滤集不可由此工具直接恢复。"
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
        # 同 import_selected：触发外部抓取并写入
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
    ))

