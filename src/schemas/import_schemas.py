"""
导入相关的 Schema 定义
"""
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class DanmakuEpisodeCreate(BaseModel):
    """保存弹幕时的明确建集信息，不携带会话或可执行回调。"""

    anime_id: int = Field(..., gt=0)
    source_id: int = Field(..., gt=0)
    episode_index: int = Field(..., ge=1)
    title: str
    provider_episode_id: str
    url: Optional[str] = None
    update_existing_title: bool = False
    skip_existing: bool = False
    # 媒体关联与建集、弹幕保存共用事务，避免保存失败后留下独立关联。
    media_server_episode_id: Optional[str] = None



class AutoImportMediaType(str, Enum):
    """自动导入媒体类型"""
    TV_SERIES = "tv_series"
    MOVIE = "movie"


class AutoImportSearchType(str, Enum):
    """自动导入搜索类型"""
    KEYWORD = "keyword"
    TMDB = "tmdb"
    TVDB = "tvdb"
    DOUBAN = "douban"
    IMDB = "imdb"
    BANGUMI = "bangumi"


class ProviderEpisodeInfo(BaseModel):
    """来自外部数据源的单个分集信息"""
    provider: str
    episodeId: str
    title: str
    episodeIndex: int
    url: Optional[str] = None


class EditedImportRequest(BaseModel):
    """编辑后的导入请求"""
    provider: str
    mediaId: str
    animeTitle: str
    mediaType: str  # 'tv_series' 或 'movie'
    season: Optional[int] = None
    year: Optional[int] = None
    imageUrl: Optional[str] = None
    episodes: List[ProviderEpisodeInfo]

    # 可选的元数据 ID
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    imdbId: Optional[str] = None
    doubanId: Optional[str] = None
    bangumiId: Optional[str] = None


class ControlAutoImportRequest(BaseModel):
    """自动导入请求模型"""
    searchType: AutoImportSearchType = Field(..., description="搜索类型: keyword/tmdb/tvdb/douban/imdb/bangumi")
    searchTerm: str = Field(..., description="搜索内容，根据searchType可以是关键词或ID")
    season: Optional[int] = Field(None, description="季度号（若未提供，自动推断或默认1）")
    episode: Optional[str] = Field(None, description="集数，支持单集('1')或多集('1,3,5,7,9,11-13')格式")
    mediaType: Optional[AutoImportMediaType] = Field(None, description="媒体类型: tv_series/movie")
    preassignedAnimeId: Optional[int] = Field(None, description="预分配的anime_id（用于匹配后备）")
    enableIncrementalRefresh: Optional[bool] = Field(None, description="任务完成后是否自动开启增量追更")

