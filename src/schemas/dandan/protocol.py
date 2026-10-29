"""弹弹Play 协议模型：从同名旧模块迁入，避免被包入口遮蔽。"""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class DandanResponseBase(BaseModel):
    """模仿 dandanplay API v2 的基础响应模型。"""
    success: bool = True
    errorCode: int = 0
    errorMessage: str = Field("", description="错误信息")


class DandanEpisodeInfo(BaseModel):
    """分集搜索信息，保留库内及并行搜索标记。"""
    episodeId: int
    episodeTitle: str
    isLibrary: bool = True
    episodeIndex: Optional[int] = None


class DandanAnimeInfo(BaseModel):
    """包含分集列表的番剧信息。"""
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
    isParallelResult: bool = False
    parallelProvider: str = ""
    parallelYear: Optional[int] = None


class DandanSearchEpisodesResponse(DandanResponseBase):
    """分集搜索响应。"""
    hasMore: bool = False
    animes: List[DandanAnimeInfo]


class DandanSearchAnimeItem(BaseModel):
    """番剧搜索结果，允许后备搜索没有库内编号。"""
    animeId: Optional[int] = None
    bangumiId: Optional[str] = ""
    animeTitle: str
    type: str
    typeDescription: str
    imageUrl: Optional[str] = None
    startDate: Optional[str] = None
    year: Optional[int] = None
    episodeCount: int
    rating: float = 0.0
    isFavorited: bool = False
    recognitionTitle: Optional[str] = None


class DandanSearchAnimeResponse(DandanResponseBase):
    """番剧搜索响应。"""
    animes: List[DandanSearchAnimeItem]


class BangumiTitle(BaseModel):
    """番剧多语言标题。"""
    language: str
    title: str


class BangumiEpisodeSeason(BaseModel):
    """番剧季度信息。"""
    id: str
    airDate: Optional[datetime] = None
    name: str
    episodeCount: int
    summary: str


class BangumiEpisode(BaseModel):
    """番剧分集详情。"""
    seasonId: Optional[str] = None
    episodeId: int
    episodeTitle: str
    episodeNumber: str
    lastWatched: Optional[datetime] = None
    airDate: Optional[datetime] = None


class BangumiIntro(BaseModel):
    """番剧简介信息。"""
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
    """番剧标签。"""
    id: int
    name: str
    count: int


class BangumiOnlineDatabase(BaseModel):
    """外部数据库链接。"""
    name: str
    url: str


class BangumiTrailer(BaseModel):
    """番剧预告信息。"""
    id: int
    url: str
    title: str
    imageUrl: str
    date: datetime


class BangumiDetails(BangumiIntro):
    """番剧详情，保持原协议字段和默认值。"""
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
    """番剧详情响应。"""
    bangumi: Optional[BangumiDetails] = None


class DandanMatchInfo(BaseModel):
    """文件匹配结果。"""
    episodeId: int
    animeId: int
    animeTitle: str
    episodeTitle: str
    type: str
    typeDescription: str
    shift: int = 0
    imageUrl: Optional[str] = None


class DandanMatchResponse(DandanResponseBase):
    """文件匹配响应。"""
    isMatched: bool = False
    matches: List[DandanMatchInfo] = []


class DandanBatchMatchRequestItem(BaseModel):
    """批量匹配的单个文件请求。"""
    fileName: str
    fileHash: Optional[str] = None
    fileSize: Optional[int] = None
    videoDuration: Optional[int] = None
    matchMode: Optional[str] = "hashAndFileName"


class DandanBatchMatchRequest(BaseModel):
    """批量匹配请求。"""
    requests: List[DandanBatchMatchRequestItem]
