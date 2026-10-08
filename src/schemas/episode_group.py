"""
TMDB 剧集组 Schema 定义
用于剧集组管理功能
"""

from typing import List, Optional
from pydantic import BaseModel


class TMDBEpisodeInGroupDetail(BaseModel):
    """TMDB 剧集组中的单集信息"""
    id: int
    name: str
    episodeNumber: int
    seasonNumber: int
    order: int = 0


class TMDBGroupInGroupDetail(BaseModel):
    """TMDB 剧集组中的分组信息"""
    id: str
    name: str
    order: int
    episodes: List[TMDBEpisodeInGroupDetail]


class TMDBEpisodeGroupDetails(BaseModel):
    """TMDB 完整剧集组详情"""
    id: str
    name: str
    description: str
    type: int = 1
    episodeCount: int
    groupCount: int
    groups: List[TMDBGroupInGroupDetail]


__all__ = [
    "TMDBEpisodeInGroupDetail",
    "TMDBGroupInGroupDetail", 
    "TMDBEpisodeGroupDetails"
]
