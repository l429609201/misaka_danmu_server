"""
Control API - 分集管理相关模型
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class EpisodeDetail(BaseModel):
    """分集详情"""
    episodeId: int
    title: str
    episodeIndex: int
    sourceUrl: Optional[str] = None
    fetchedAt: Optional[datetime] = None
    commentCount: int
    danmakuFilePath: Optional[str] = None


class EpisodeInfoUpdate(BaseModel):
    """用于更新分集信息的模型"""
    title: str = Field(..., min_length=1, description="新的分集标题")
    episodeIndex: int = Field(..., ge=1, description="新的集数")
    sourceUrl: Optional[str] = Field(None, description="新的官方链接")
    danmakuFilePath: Optional[str] = Field(None, description="弹幕文件路径")


class PaginatedEpisodesResponse(BaseModel):
    """用于分集列表分页的响应模型"""
    total: int
    list: List[EpisodeDetail]


class EpisodeOffsetRequest(BaseModel):
    """分集偏移请求"""
    episodeIds: List[int]
    offset: int


class BulkDeleteEpisodesRequest(BaseModel):
    """批量删除分集请求"""
    episodeIds: List[int]


class BulkDeleteRequest(BaseModel):
    """批量删除数据源请求"""
    sourceIds: List[int]


__all__ = [
    "EpisodeDetail",
    "EpisodeInfoUpdate",
    "PaginatedEpisodesResponse",
    "EpisodeOffsetRequest",
    "BulkDeleteEpisodesRequest",
    "BulkDeleteRequest",
]
