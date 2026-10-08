"""
Control API - 媒体库相关模型
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class LibrarySourceBrief(BaseModel):
    """媒体库列表中的简化源信息，用于快速操作标记和追更。"""
    sourceId: int
    providerName: str
    isFavorited: bool
    incrementalRefreshEnabled: bool
    isFinished: bool = False


class LibraryAnimeInfo(BaseModel):
    """代表媒体库中的一个番剧条目。"""
    animeId: int
    localImagePath: Optional[str] = None
    imageUrl: Optional[str] = None
    title: str
    type: str
    season: int
    year: Optional[int] = None
    # schemas 顶层导出的媒体库模型也需接受数据库中的未知集数，保留 None 语义。
    episodeCount: Optional[int] = None
    sourceCount: int
    createdAt: datetime
    groupId: Optional[int] = None
    groupName: Optional[str] = None
    sources: List[LibrarySourceBrief] = []  # 简化的源列表，用于快速操作


class LibraryResponse(BaseModel):
    """媒体库响应"""
    total: int
    list: List[LibraryAnimeInfo]


__all__ = [
    "LibrarySourceBrief",
    "LibraryAnimeInfo",
    "LibraryResponse",
]
