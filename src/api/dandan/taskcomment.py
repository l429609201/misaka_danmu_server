"""
弹幕异步任务轮询接口

GET /taskcomment/{taskId} — 轮询弹幕下载任务状态。
任务完成后返回 episodeId，客户端用此 ID 调 /comment/{episodeId} 获取弹幕。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Path

# 轮询响应使用协议 Schema，不再依赖已删除的数据库模型兼容层。
from src.schemas.dandan import comment as models
from src.services.service_container import get_database_service

from .route_handler import get_token_from_path, DandanApiRoute

logger = logging.getLogger(__name__)

taskcomment_router = APIRouter(route_class=DandanApiRoute)


def _parse_episode_id_from_unique_key(unique_key: Optional[str]) -> Optional[int]:
    """从 uniqueKey 解析 episodeId"""
    if not unique_key:
        return None
    for prefix in ("match_fallback_comments_", "fallback_comments_"):
        if unique_key.startswith(prefix):
            try:
                return int(unique_key[len(prefix):])
            except ValueError:
                return None
    return None


@taskcomment_router.get(
    "/taskcomment/{taskId}",
    response_model=models.TaskCommentResponse,
    response_model_exclude_none=True,
    summary="[Misaka扩展] 轮询弹幕异步任务状态"
)
async def poll_danmaku_task(
    taskId: str = Path(..., description="由 /comment/{episodeId}?async=1 返回的任务ID"),
    token: str = Depends(get_token_from_path),
):
    """
    轮询弹幕下载任务状态，不返回弹幕内容。

    - **pending**：任务仍在执行，返回 progress/description，客户端继续轮询
    - **completed**：任务完成，返回 episodeId，客户端用此 ID 调 /comment/{episodeId} 获取弹幕
    - **failed**：任务失败，返回 description 说明原因
    """
    # 通过 DatabaseService 查询任务记录
    db = get_database_service()
    async with db.transaction():
        task = await db.task.get_by_id(taskId)

    if not task:
        return models.TaskCommentResponse(
            status="failed", taskId=taskId,
            description="任务不存在或已过期",
        )

    # 解析 episodeId（所有状态都返回，方便客户端使用）
    episode_id = _parse_episode_id_from_unique_key(task.uniqueKey)

    # 中文状态映射
    status_map = {'排队中': 'pending', '运行中': 'pending', '已暂停': 'pending', '已完成': 'completed', '失败': 'failed'}
    mapped_status = status_map.get(task.status, 'failed')

    return models.TaskCommentResponse(
        status=mapped_status,
        taskId=taskId,
        episodeId=episode_id,
        progress=task.progress,
        description=task.description,
    )
