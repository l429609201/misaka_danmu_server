"""
弹弹Play 兼容 API 的 Pydantic 模型

使用方式:
    from src.api.dandan.models import (
        DandanResponseBase, DandanSearchEpisodesResponse, DandanMatchResponse
    )
"""

from typing import List, Optional, Dict
from datetime import datetime
from pydantic import BaseModel, Field


class DandanResponseBase(BaseModel):
    """模仿 dandanplay API v2 的基础响应模型"""
    success: bool = True
    errorCode: int = 0
    errorMessage: str = Field("", description="错误信息")


class DandanEpisodeInfo(BaseModel):
    """dandanplay /search/episodes 接口中的分集信息模型"""
    episodeId: int
    episodeTitle: str
    isLibrary: bool = True  # True=库内已有，False=源站补充（并行搜索）
    episodeIndex: Optional[int] = None  # 集数索引，仅并行搜索时使用


class DandanAnimeInfo(BaseModel):
    """dandanplay /search/episodes 接口中的番剧信息模型"""
    animeId: int
    animeTitle: str
    imageUrl: str = ""
    searchKeyword: str = ""
    type: str
    typeDescription: str
    isOnAir: bool = False
    airDay: int = 0
    isFavorited: bool = False
    rating: float = 0.0
    episodes: List[DandanEpisodeInfo]
    isParallelResult: bool = False  # True=并行搜索结果（含源站补充分集）
    parallelProvider: str = ""    # 并行搜索的来源名称
    parallelYear: Optional[int] = None  # 并行搜索的年份


class DandanSearchEpisodesResponse(DandanResponseBase):
    hasMore: bool = False
    animes: List[DandanAnimeInfo]


# --- Models for /search/anime ---

class DandanSearchAnimeItem(BaseModel):
    animeId: Optional[int] = None  # 支持后备搜索时animeId为None
    bangumiId: Optional[str] = ""
    animeTitle: str
    type: str
    typeDescription: str
    imageUrl: Optional[str] = None
    startDate: Optional[str] = None  # To keep compatibility, but will be populated from year
    year: Optional[int] = None
    episodeCount: int
    rating: float = 0.0
    isFavorited: bool = False
    recognitionTitle: Optional[str] = None  # 识别词反向映射命中时的入库正确名（前端展示为tag）


class DandanSearchAnimeResponse(DandanResponseBase):
    animes: List[DandanSearchAnimeItem]


# --- Models for /bangumi/{anime_id} ---

class BangumiTitle(BaseModel):
    language: str
    title: str


class BangumiEpisodeSeason(BaseModel):
    id: str
    airDate: Optional[datetime] = None
    name: str
    episodeCount: int
    summary: str


class BangumiEpisode(BaseModel):
    seasonId: Optional[str] = None
    episodeId: int
    episodeTitle: str
    episodeNumber: str
    lastWatched: Optional[datetime] = None
    airDate: Optional[datetime] = None


class BangumiIntro(BaseModel):
    animeId: int
    bangumiId: Optional[str] = ""
    animeTitle: str
    imageUrl: Optional[str] = None
    searchKeyword: Optional[str] = None
    isOnAir: bool = False
    airDay: int = 0
    isRestricted: bool = False
    rating: float = 0.0


class BangumiTag(BaseModel):
    id: int
    name: str
    count: int


class BangumiOnlineDatabase(BaseModel):
    name: str
    url: str


class BangumiTrailer(BaseModel):
    id: int
    url: str
    title: str
    imageUrl: str
    date: datetime


class BangumiDetails(BangumiIntro):
    type: str
    typeDescription: str
    titles: List[BangumiTitle] = []
    seasons: List[BangumiEpisodeSeason] = []
    episodes: List[BangumiEpisode] = []
    summary: Optional[str] = ""
    metadata: List[str] = []
    year: Optional[int] = None
    userRating: int = 0
    favoriteStatus: Optional[str] = None
    comment: Optional[str] = None
    ratingDetails: Dict[str, float] = {}
    relateds: List[BangumiIntro] = []
    similars: List[BangumiIntro] = []
    tags: List[BangumiTag] = []
    onlineDatabases: List[BangumiOnlineDatabase] = []
    trailers: List[BangumiTrailer] = []


class BangumiDetailsResponse(DandanResponseBase):
    bangumi: Optional[BangumiDetails] = None


# --- Models for /match ---

class DandanMatchInfo(BaseModel):
    episodeId: int
    animeId: int
    animeTitle: str
    episodeTitle: str
    type: str
    typeDescription: str
    shift: int = 0
    imageUrl: Optional[str] = None


class DandanMatchResponse(DandanResponseBase):
    isMatched: bool = False
    matches: List[DandanMatchInfo] = []


# --- Models for /match/batch ---

class DandanBatchMatchRequestItem(BaseModel):
    fileName: str
    fileHash: Optional[str] = None
    fileSize: Optional[int] = None
    videoDuration: Optional[int] = None
    matchMode: Optional[str] = "hashAndFileName"


class DandanBatchMatchRequest(BaseModel):
    requests: List[DandanBatchMatchRequestItem]

