"""
元数据相关的 Pydantic 模型

从 src/db/models.py 迁移而来
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TMDBSeasonInfo(BaseModel):
    """TMDB 季度信息"""
    id: int
    name: str
    overview: Optional[str] = None
    season_number: int = Field(..., alias="seasonNumber")
    episode_count: int = Field(..., alias="episodeCount")
    air_date: Optional[str] = Field(None, alias="airDate")
    poster_path: Optional[str] = Field(None, alias="posterPath")
    aliases: Optional[List[str]] = Field(default=[], description="季度别名列表")

    class Config:
        populate_by_name = True


class MetadataDetailsResponse(BaseModel):
    """所有元数据源详情接口的统一响应模型"""
    id: str
    title: str
    type: Optional[str] = None
    tmdbId: Optional[str] = None
    imdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    bangumiId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasesCn: List[str] = []
    aliasesJp: List[str] = []
    imageUrl: Optional[str] = None
    details: Optional[str] = None
    year: Optional[int] = None
    tmdbSeasons: Optional[List[TMDBSeasonInfo]] = None
    provider: Optional[str] = None
    supportsEpisodeUrls: bool = False
    # 保留源站扩展信息，供缓存还原、平台探测和分集补充使用。
    extra: Optional[Dict[str, Any]] = Field(default_factory=dict)


class MetadataSourceStatusResponse(BaseModel):
    """元数据源状态响应"""
    model_config = {"extra": "allow"}

    providerName: str
    isAuxSearchEnabled: bool
    isFailoverEnabled: bool
    displayOrder: int
    status: str
    statusCode: str = "ok"
    useProxy: bool
    logRawResponses: bool = Field(False, alias="log_raw_responses")


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
    """TMDB 剧集组中的分组详情"""
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
    """增强的 TMDB 剧集详情（包含中文名和日文名）"""
    id: int
    name: str  # Chinese name
    episodeNumber: int
    seasonNumber: int
    airDate: Optional[str] = None
    overview: Optional[str] = ""
    order: int
    nameJp: Optional[str] = None
    imageUrl: Optional[str] = None


class EnrichedTMDBGroupInGroupDetail(BaseModel):
    """增强的 TMDB 分组详情"""
    id: str
    name: str
    order: int
    episodes: List[EnrichedTMDBEpisodeInGroupDetail]


class EnrichedTMDBEpisodeGroupDetails(TMDBEpisodeGroupDetails):
    """增强的 TMDB 剧集组详情"""
    groups: List[EnrichedTMDBGroupInGroupDetail]
