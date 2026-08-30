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

from src.db import crud
from ..security_gateway import ToolPermission
from .base import Tool, registry

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
