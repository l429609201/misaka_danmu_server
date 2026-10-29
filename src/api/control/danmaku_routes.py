"""
外部控制API - 弹幕管理路由
包含: /danmaku/{episodeId} GET/POST
"""

from typing import Awaitable, Callable, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from src.schemas.comments import Comment, CommentsResponse, DanmakuUpdateRequest
from src.schemas.control import ControlTaskResponse
from src.services.task_manager import TaskManager
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.services.config_service import ConfigService
from src.services.service_container import get_database_service
from src.workflows.danmaku_management import overwrite_episode_comments, read_episode_comments
from .dependencies import get_task_manager, get_config_service

router = APIRouter()


@router.get("/danmaku/{episodeId}", response_model=CommentsResponse, summary="获取弹幕")
async def get_danmaku(episodeId: int) -> CommentsResponse:
    """获取指定分集的全部弹幕，用于弹幕调整，不受输出限制控制。"""
    # 文件读取由编排层管理短事务，不注入请求生命周期的数据库会话。
    comments = await read_episode_comments(get_database_service(), episodeId)
    if comments is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分集未找到")
    return CommentsResponse(count=len(comments), comments=[Comment.model_validate(c) for c in comments])


@router.post("/danmaku/{episodeId}", status_code=202, summary="覆盖弹幕", response_model=ControlTaskResponse)
async def overwrite_danmaku(
    episodeId: int,
    payload: DanmakuUpdateRequest,
    task_manager: TaskManager = Depends(get_task_manager),
    config_service: ConfigService = Depends(get_config_service)
) -> Dict[str, str]:
    """提交后台任务，用请求体中的弹幕列表完全覆盖指定分集的现有弹幕。"""
    async def overwrite_task(
        _task_session: object, cb: Callable[[int, str], Awaitable[None]],
    ) -> None:
        """提交无会话依赖的覆盖任务；数据库事务由弹幕工作流统一管理。"""
        # TaskManager 为兼容既有任务工厂仍传入工作会话，但该流程不使用它。
        await cb(10, "正在准备覆盖弹幕...")
        comments_to_insert = [comment.model_dump() for comment in payload.comments]
        # 不预先清空旧数据；空列表也交由编排层完整替换并更新数量。
        await cb(50, f"正在覆盖 {len(comments_to_insert)} 条弹幕...")
        added = await overwrite_episode_comments(episodeId, comments_to_insert, config_service)
        raise TaskSuccess(f"弹幕覆盖完成，写入 {added} 条。")

    try:
        task_id, _ = await task_manager.submit_task(overwrite_task, f"外部API覆盖弹幕 (分集ID: {episodeId})")
        return {"message": "弹幕覆盖任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

