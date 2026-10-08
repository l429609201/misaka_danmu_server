"""弹幕编辑请求、响应及嵌套 DTO；集中定义以避免 API 层模型反向依赖。"""

from typing import List, Optional

from pydantic import BaseModel, Field


class TimeOffsetRequest(BaseModel):
    """时间偏移请求"""
    episodeIds: List[int] = Field(..., description="要调整的分集ID列表")
    offsetSeconds: float = Field(..., description="偏移秒数（正数延后，负数提前）")


class TimeOffsetResponse(BaseModel):
    """时间偏移响应"""
    success: bool
    modifiedCount: int = Field(0, description="修改的分集数")
    totalComments: int = Field(0, description="总共修改的弹幕数")


class SplitConfig(BaseModel):
    """拆分配置"""
    episodeIndex: int = Field(..., description="新分集的集数")
    startTime: float = Field(..., description="开始时间（秒）")
    endTime: float = Field(..., description="结束时间（秒）")
    title: Optional[str] = Field(None, description="新分集标题")


class SplitRequest(BaseModel):
    """分集拆分请求"""
    sourceEpisodeId: int = Field(..., description="源分集ID")
    splits: List[SplitConfig] = Field(..., description="拆分配置列表")
    deleteSource: bool = Field(True, description="是否删除原分集")
    resetTime: bool = Field(True, description="新分集时间是否从0开始")


class NewEpisodeInfo(BaseModel):
    """新分集信息"""
    episodeId: int
    episodeIndex: int
    commentCount: int


class SplitResponse(BaseModel):
    """分集拆分响应"""
    success: bool
    error: Optional[str] = None
    newEpisodes: List[NewEpisodeInfo] = []


class MergeSourceConfig(BaseModel):
    """合并源配置"""
    episodeId: int = Field(..., description="源分集ID")
    offsetSeconds: float = Field(0, description="时间偏移（秒）")


class MergeRequest(BaseModel):
    """分集合并请求"""
    sourceEpisodes: List[MergeSourceConfig] = Field(..., description="源分集配置列表")
    targetEpisodeIndex: int = Field(..., description="目标集数")
    targetTitle: str = Field(..., description="目标标题")
    deleteSources: bool = Field(True, description="是否删除原分集")
    deduplicate: bool = Field(False, description="是否去重")


class MergeResponse(BaseModel):
    """分集合并响应"""
    success: bool
    error: Optional[str] = None
    newEpisodeId: Optional[int] = None
    commentCount: int = 0


class SourceInfo(BaseModel):
    """来源信息"""
    name: str
    count: int


class TimeRange(BaseModel):
    """时间范围"""
    start: float
    end: float


class DistributionItem(BaseModel):
    """分布项"""
    minute: int
    count: int


class CommentPreview(BaseModel):
    """弹幕预览"""
    time: float
    content: str
    source: str


class DanmakuDetailResponse(BaseModel):
    """弹幕详情响应"""
    episodeId: int
    totalCount: int
    timeRange: TimeRange
    sources: List[SourceInfo]
    distribution: List[DistributionItem]
    comments: List[CommentPreview]


class CommentsPageResponse(BaseModel):
    """弹幕分页响应"""
    total: int
    comments: List[CommentPreview]
    page: int
    pageSize: int
