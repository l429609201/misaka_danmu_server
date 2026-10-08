"""Control API 导入路由专用请求与响应模型。"""

from typing import List, Optional, Union

from pydantic import BaseModel, Field

from src.schemas.search import ProviderEpisodeInfo
from src.schemas.ui.search import ProviderSearchInfo as UIProviderSearchInfo


class ControlTaskResponse(BaseModel):
    """任务提交成功响应。"""

    status: str = "success"
    message: str
    taskId: str


class ControlSearchResultItem(UIProviderSearchInfo):
    """带结果索引的搜索结果。"""

    resultIndex: int = Field(..., alias="result_index", ge=0)

    class Config:
        populate_by_name = True


class ControlSearchResponse(BaseModel):
    """媒体搜索响应。"""

    searchId: str
    results: List[ControlSearchResultItem]
    errors: List[str] = Field(default_factory=list)


class ControlDirectImportRequest(BaseModel):
    """直接导入搜索结果请求。"""

    searchId: str
    resultIndex: int = Field(..., alias="result_index", ge=0)
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    imdbId: Optional[str] = None
    doubanId: Optional[str] = None

    class Config:
        populate_by_name = True


class ControlEditedImportRequest(BaseModel):
    """编辑分集后导入请求。"""

    searchId: str
    resultIndex: int = Field(..., alias="result_index", ge=0)
    title: Optional[str] = None
    episodes: List[ProviderEpisodeInfo]
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    imdbId: Optional[str] = None
    doubanId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = None

    class Config:
        populate_by_name = True


class ControlUrlImportRequest(BaseModel):
    """从视频页面 URL 导入弹幕请求。"""

    sourceId: int
    episodeIndex: int = Field(..., alias="episode_index", gt=0)
    url: str
    title: Optional[str] = None

    class Config:
        populate_by_name = True


class ControlXmlImportRequest(BaseModel):
    """从 XML 或纯文本导入弹幕请求。"""

    sourceId: int
    episodeIndex: int = Field(..., alias="episode_index", gt=0)
    content: str
    title: Optional[str] = None

    class Config:
        populate_by_name = True


class EpisodesWithFilteredResponse(BaseModel):
    """保留分集与被规则过滤分集的组合响应。"""

    episodes: List[ProviderEpisodeInfo]
    filteredEpisodes: List[ProviderEpisodeInfo] = Field(default_factory=list)


class ControlRateLimitProviderStatusResponse(BaseModel):
    """外部控制接口的单个源流控状态。"""

    providerName: str
    directCount: int
    fallbackCount: int
    requestCount: int
    quota: Union[int, str]


class ControlRateLimitStatusApiResponse(BaseModel):
    """外部控制接口的完整流控状态响应。"""

    globalEnabled: bool
    globalRequestCount: int
    globalLimit: int
    globalPeriod: str
    secondsUntilReset: int
    fallbackTotalCount: int
    fallbackTotalLimit: int
    fallbackMatchCount: int
    fallbackSearchCount: int
    providers: List[ControlRateLimitProviderStatusResponse]
