"""
动漫管理相关的 Pydantic 模型

从 src/db/models.py 迁移而来
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class AnimeCreate(BaseModel):
    """创建动漫条目的请求模型"""
    title: str = Field(..., min_length=1, description="影视名称")
    type: str = Field(..., description="媒体类型: tv_series 或 movie")
    season: int = Field(..., ge=0, description="季度")
    year: Optional[int] = Field(None, description="发行年份")
    tmdbId: Optional[str] = Field(None, description="TMDB ID")
    tvdbId: Optional[str] = Field(None, description="TVDB ID")
    doubanId: Optional[str] = Field(None, description="豆瓣 ID")
    imdbId: Optional[str] = Field(None, description="IMDB ID")
    bangumiId: Optional[str] = Field(None, description="Bangumi ID")
    nameEn: Optional[str] = Field(None, description="英文标题")
    nameJp: Optional[str] = Field(None, description="日文标题")
    nameRomaji: Optional[str] = Field(None, description="罗马音标题")
    aliasCn1: Optional[str] = Field(None, description="中文别名1")
    aliasCn2: Optional[str] = Field(None, description="中文别名2")
    aliasCn3: Optional[str] = Field(None, description="中文别名3")
    imageUrl: Optional[str] = Field(None, description="封面图片URL")


class AnimeBasicInfo(BaseModel):
    """动漫基本信息"""
    animeId: int
    title: str
    type: str
    season: int
    imageUrl: Optional[str] = None


class AnimeDetail(BaseModel):
    """动漫详情（用于编辑页面）"""
    animeId: int
    title: str
    type: str
    season: int
    year: Optional[int] = None
    imageUrl: Optional[str] = None
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    imdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasCn1: Optional[str] = None
    aliasCn2: Optional[str] = None
    aliasCn3: Optional[str] = None
    aliasLocked: Optional[bool] = None
    tmdbEpisodeGroupId: Optional[str] = None
    episodeCount: int
    sourceCount: int
    createdAt: datetime
    mediaServerType: Optional[str] = None
    mediaServerSeriesId: Optional[str] = None
    mediaServerSeasonId: Optional[str] = None


class AnimeDetailUpdate(BaseModel):
    """更新动漫信息的请求模型"""
    title: str = Field(..., min_length=1, description="新的影视名称")
    type: str = Field(..., description="媒体类型")
    season: int = Field(..., ge=0, description="新的季度")
    episodeCount: Optional[int] = Field(None, ge=1, description="新的集数")
    year: Optional[int] = Field(None, description="发行年份")
    tmdbId: Optional[str] = Field(None, description="TMDB ID")
    tvdbId: Optional[str] = Field(None, description="TVDB ID")
    doubanId: Optional[str] = Field(None, description="豆瓣 ID")
    imdbId: Optional[str] = Field(None, description="IMDB ID")
    bangumiId: Optional[str] = Field(None, description="Bangumi ID")
    tmdbEpisodeGroupId: Optional[str] = Field(None, description="TMDB 剧集组 ID")
    nameEn: Optional[str] = Field(None, description="英文标题")
    nameJp: Optional[str] = Field(None, description="日文标题")
    nameRomaji: Optional[str] = Field(None, description="罗马音标题")
    aliasCn1: Optional[str] = Field(None, description="中文别名1")
    aliasCn2: Optional[str] = Field(None, description="中文别名2")
    aliasCn3: Optional[str] = Field(None, description="中文别名3")
    imageUrl: Optional[str] = Field(None, description="封面图片URL")
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
    imageUrl: Optional[str] = None
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    imdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasCn1: Optional[str] = None
    aliasCn2: Optional[str] = None
    aliasCn3: Optional[str] = None
    aliasLocked: Optional[bool] = None
    episodeCount: int
    createdAt: datetime
    mediaServerType: Optional[str] = None
    mediaServerSeriesId: Optional[str] = None
    mediaServerSeasonId: Optional[str] = None


class EpisodeInfoUpdate(BaseModel):
    """用于更新分集信息的模型"""
    title: str = Field(..., min_length=1, description="新的分集标题")
    episodeIndex: int = Field(..., ge=1, description="新的集数")
    sourceUrl: Optional[str] = Field(None, description="新的官方链接")
    danmakuFilePath: Optional[str] = Field(None, description="弹幕文件路径")


class SourceCreate(BaseModel):
    """创建数据源的请求模型"""
    providerName: str = Field(..., min_length=1, description="数据源提供方名称")
    mediaId: str = Field(..., min_length=1, description="数据源媒体ID")
