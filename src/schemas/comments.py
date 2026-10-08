"""
弹幕相关的 Pydantic 模型

从 src/db/models.py 迁移而来
"""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class Comment(BaseModel):
    """单条弹幕模型"""
    cid: int = Field(..., description="弹幕ID (数据库主键)")
    p: str = Field(..., description="弹幕参数：播放 API 为 time,mode,color,[source]；管理读写为九段 XML 格式，来源在第七段")
    m: str = Field(..., description="弹幕内容")


class CommentsResponse(BaseModel):
    """弹幕响应模型（原 CommentResponse）"""
    count: int = Field(..., description="弹幕总数")
    comments: List[Comment] = Field([], description="弹幕列表")
    # 异步任务扩展字段（同步模式下均为 null，向后兼容）
    status: Optional[str] = Field(None, description="任务状态: pending | completed | failed（同步模式为 null）")
    taskId: Optional[str] = Field(None, description="异步任务ID（同步模式为 null）")
    episodeId: Optional[int] = Field(None, description="关联的分集ID（轮询接口返回，客户端用此ID调 /comment/{episodeId} 获取弹幕）")
    progress: Optional[int] = Field(None, description="任务进度 0-100")
    description: Optional[str] = Field(None, description="任务描述/进度信息")


class TaskCommentResponse(BaseModel):
    """轮询接口专用响应模型，只返回有值的字段"""
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(..., description="任务状态: pending | completed | failed")
    taskId: str = Field(..., description="任务ID")
    episodeId: Optional[int] = Field(None, description="关联的分集ID（任务完成后用此ID调 /comment/{episodeId} 获取弹幕）")
    progress: Optional[int] = Field(None, description="任务进度 0-100")
    description: Optional[str] = Field(None, description="任务进度描述")


class DanmakuUpdateRequest(BaseModel):
    """用于覆盖弹幕的请求体模型"""
    comments: List[Comment]
