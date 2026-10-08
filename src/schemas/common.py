"""
通用模型
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# 弹幕源与服务之间的公共 API 契约版本。
SCRAPER_API_VERSION = 2


class PaginatedCommentResponse(BaseModel):
    """用于UI弹幕列表分页的响应模型"""
    total: int
    list: List[Any]  # Comment 已在 dandan/comment.py 中定义


class ScheduledTaskInfo(BaseModel):
    """定时任务信息"""
    taskId: str
    name: str
    jobType: str
    cronExpression: str
    isEnabled: bool
    taskConfig: dict = {}
    lastRunAt: Optional[datetime] = None
    nextRunAt: Optional[datetime] = None
    isSystemTask: bool = False


# --- TMDB API Models ---
class TMDBEpisodeInGroupDetail(BaseModel):
    """TMDB 剧集组中的分集详情"""
    id: int
    name: str
    episodeNumber: int
    seasonNumber: int
    airDate: Optional[str] = None
    overview: Optional[str] = ""
    order: int


class TMDBGroupInGroupDetail(BaseModel):
    """TMDB 剧集组中的组详情"""
    id: str
    name: str
    order: int
    episodes: List[TMDBEpisodeInGroupDetail]


class TMDBEpisodeGroupDetails(BaseModel):
    """TMDB 剧集组详情"""
    id: str
    name: str
    description: Optional[str] = ""
    episodeCount: int
    groupCount: int
    groups: List[TMDBGroupInGroupDetail]
    network: Optional[Dict[str, Any]] = None
    type: int


class EnrichedTMDBEpisodeInGroupDetail(BaseModel):
    """增强的 TMDB 分集详情（包含中文名）"""
    id: int
    name: str  # This will be the Chinese name
    episodeNumber: int
    seasonNumber: int
    airDate: Optional[str] = None
    overview: Optional[str] = ""
    order: int
    nameJp: Optional[str] = None
    imageUrl: Optional[str] = None


class EnrichedTMDBGroupInGroupDetail(BaseModel):
    """增强的 TMDB 组详情"""
    id: str
    name: str
    order: int
    episodes: List[EnrichedTMDBEpisodeInGroupDetail]


class EnrichedTMDBEpisodeGroupDetails(TMDBEpisodeGroupDetails):
    """增强的 TMDB 剧集组详情"""
    groups: List[EnrichedTMDBGroupInGroupDetail]


__all__ = [
    "PaginatedCommentResponse",
    "ScheduledTaskInfo",
    "TMDBEpisodeInGroupDetail",
    "TMDBGroupInGroupDetail",
    "TMDBEpisodeGroupDetails",
    "EnrichedTMDBEpisodeInGroupDetail",
    "EnrichedTMDBGroupInGroupDetail",
    "EnrichedTMDBEpisodeGroupDetails",
]
