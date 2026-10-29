"""
Control API - 番剧管理相关模型
"""
from typing import Optional
from pydantic import BaseModel, Field


class AnimeCreate(BaseModel):
    """Model for creating a new anime entry manually."""
    title: str = Field(..., description="作品标题")
    type: str = Field("tv_series", description="作品类型 (tv_series, movie, ova, other)")
    season: int = Field(1, description="季度")
    year: Optional[int] = Field(None, description="年份")
    imageUrl: Optional[str] = Field(None, description="海报图片URL")


class AnimeDetailUpdate(BaseModel):
    """用于更新番剧详细信息的模型"""
    title: str = Field(..., min_length=1, description="新的影视名称")
    type: str
    season: int = Field(..., ge=0, description="新的季度")
    year: Optional[int] = Field(None, description="发行年份")
    episodeCount: Optional[int] = Field(None, ge=1, description="新的集数")
    imageUrl: Optional[str] = None
    tmdbId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = None
    bangumiId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    imdbId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasCn1: Optional[str] = None
    aliasCn2: Optional[str] = None
    aliasCn3: Optional[str] = None
    aliasLocked: Optional[bool] = Field(None, description="别名是否锁定")
    mediaServerType: Optional[str] = Field(None, description="媒体服务器类型：emby/jellyfin/plex")
    mediaServerSeriesId: Optional[str] = Field(None, description="媒体服务器 Series/Movie 级 ID")
    mediaServerSeasonId: Optional[str] = Field(None, description="媒体服务器 Season 级 ID")


class AnimeFullDetails(BaseModel):
    """用于返回番剧完整信息的模型"""
    animeId: int
    title: str
    type: str
    season: int
    year: Optional[int] = None
    episodeCount: Optional[int] = None
    localImagePath: Optional[str] = None
    imageUrl: Optional[str] = None
    tmdbId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = None
    bangumiId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    imdbId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasCn1: Optional[str] = None
    aliasCn2: Optional[str] = None
    aliasCn3: Optional[str] = None
    aliasLocked: Optional[bool] = False
    mediaServerType: Optional[str] = None
    mediaServerSeriesId: Optional[str] = None
    mediaServerSeasonId: Optional[str] = None


__all__ = [
    "AnimeCreate",
    "AnimeDetailUpdate",
    "AnimeFullDetails",
]
