"""
御坂助手 · 写类工具（P3）
------------------------------------------------------------
写类工具权限为 WRITE，必须经用户二次确认才执行（确认逻辑在 agent 层）。
执行时从 context 取所需管理器（app.state 注入）。

首个写工具：刷新分集弹幕（提交后台任务，不直接改库，相对安全）。
"""

import logging
from typing import Any, Dict

from src.db import crud
from src import tasks
from ..security_gateway import ToolPermission
from .base import Tool, registry

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
