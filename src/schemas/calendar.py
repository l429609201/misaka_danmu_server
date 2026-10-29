"""日历订阅接口和编排共用的数据契约。"""

from typing import Optional

from pydantic import BaseModel


class SubscribeRequest(BaseModel):
    """单条外部日历订阅。"""

    animeTitle: str
    mediaType: str = "tv_series"
    season: Optional[int] = None
    traktTmdbId: Optional[str] = None
    traktId: Optional[str] = None
    bangumiId: Optional[str] = None
    provider: Optional[str] = None
    externalId: Optional[str] = None
    runNow: bool = True
    selectedEpisodes: Optional[list[str]] = None


class BatchSubscribeRequest(BaseModel):
    """批量外部日历订阅。"""

    items: list[SubscribeRequest]
    runNow: bool = True


class UnsubscribeRequest(BaseModel):
    """取消订阅或本地追更的标识。"""

    provider: Optional[str] = None
    externalId: Optional[str] = None
    sourceId: Optional[int] = None
    bangumiId: Optional[str] = None
    traktId: Optional[str] = None
    traktTmdbId: Optional[str] = None
