"""
任务弹幕轮询业务流程
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm_models import TaskHistory
from src.schemas.comments import TaskCommentResponse
from .helpers import parse_episode_id_from_unique_key

logger = logging.getLogger(__name__)


async def poll_danmaku_task_flow(task_id: str, session: AsyncSession) -> TaskCommentResponse:
    """
    轮询弹幕下载任务状态，不返回弹幕内容。

    Args:
        task_id: 任务ID
        session: 数据库会话

    Returns:
        TaskCommentResponse 对象，包含任务状态与相关信息

    状态说明：
    - **pending**：任务仍在执行，附带 progress/description，客户端继续轮询
    - **completed**：任务完成，附带 episodeId，客户端用此 ID 调 /comment/{episodeId} 获取弹幕
    - **failed**：任务失败，附带 description 说明原因
    """
    # 查询任务记录
    stmt = select(TaskHistory).where(TaskHistory.taskId == task_id)
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()

    if not task:
        return TaskCommentResponse(
            status="failed",
            taskId=task_id,
            description="任务不存在或已过期",
        )

    # 解析 episodeId：所有状态都附带，方便客户端直接使用
    episode_id = parse_episode_id_from_unique_key(task.uniqueKey)

    # 中文状态映射到对外状态
    status_map = {
        "排队中": "pending",
        "运行中": "pending",
        "已暂停": "pending",
        "已完成": "completed",
        "失败": "failed",
    }
    mapped_status = status_map.get(task.status, "failed")

    return TaskCommentResponse(
        status=mapped_status,
        taskId=task_id,
        episodeId=episode_id,
        progress=task.progress,
        description=task.description,
    )
