"""
Control API - 关联冲突相关模型
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class ReassociationRequest(BaseModel):
    """关联请求"""
    targetAnimeId: int


class ConflictEpisode(BaseModel):
    """冲突分集信息"""
    episodeIndex: int
    sourceEpisodeId: int
    targetEpisodeId: int
    sourceDanmakuCount: int
    targetDanmakuCount: int
    sourceLastFetchTime: Optional[datetime]
    targetLastFetchTime: Optional[datetime]


class ProviderConflict(BaseModel):
    """提供商冲突信息"""
    providerName: str
    sourceSourceId: int
    targetSourceId: int
    conflictEpisodes: List[ConflictEpisode]


class ReassociationConflictResponse(BaseModel):
    """关联冲突检测响应"""
    hasConflict: bool
    conflicts: List[ProviderConflict]


class EpisodeResolution(BaseModel):
    """分集冲突解决方案"""
    episodeIndex: int
    keepSource: bool  # True=保留源分集, False=保留目标分集


class ProviderResolution(BaseModel):
    """提供商冲突解决方案"""
    providerName: str
    episodeResolutions: List[EpisodeResolution]
    sourceOffset: int = 0  # 源番剧集数偏移


class ReassociationResolveRequest(BaseModel):
    """关联冲突解决请求"""
    targetAnimeId: int
    resolutions: List[ProviderResolution]


__all__ = [
    "ReassociationRequest",
    "ConflictEpisode",
    "ProviderConflict",
    "ReassociationConflictResponse",
    "EpisodeResolution",
    "ProviderResolution",
    "ReassociationResolveRequest",
]
