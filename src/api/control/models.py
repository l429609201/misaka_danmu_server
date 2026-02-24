"""
外部控制API的Pydantic模型定义
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator

from src.db import models


class AutoImportSearchType(str, Enum):
    """自动导入搜索类型"""
    KEYWORD = "keyword"
    TMDB = "tmdb"
    TVDB = "tvdb"
    DOUBAN = "douban"
    IMDB = "imdb"
    BANGUMI = "bangumi"


class AutoImportMediaType(str, Enum):
    """自动导入媒体类型"""
    TV_SERIES = "tv_series"
    MOVIE = "movie"


class ControlActionResponse(BaseModel):
    """通用操作成功响应模型"""
    message: str


class ControlTaskResponse(BaseModel):
    """任务提交成功响应模型"""
    message: str
    taskId: str


class ExecutionTaskResponse(BaseModel):
    """用于返回执行任务ID的响应模型"""
    schedulerTaskId: str
    executionTaskId: Optional[str] = None
    status: Optional[str] = Field(None, description="执行任务状态: 运行中/已完成/失败/已取消/等待中/已暂停")


class ControlSearchResultItem(models.ProviderSearchInfo):
    """搜索结果项，包含结果索引"""
    resultIndex: int = Field(..., alias="result_index", description="结果在列表中的顺序索引，从0开始")

    class Config:
        populate_by_name = True


class ControlSearchResponse(BaseModel):
    """搜索响应模型"""
    searchId: str = Field(..., description="本次搜索操作的唯一ID，用于后续操作")
    results: List[ControlSearchResultItem] = Field(..., description="搜索结果列表")


class ControlDirectImportRequest(BaseModel):
    """直接导入请求模型"""
    searchId: str = Field(..., description="来自搜索响应的searchId")
    resultIndex: int = Field(..., alias="result_index", ge=0, description="要导入的结果的索引 (从0开始)")
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    imdbId: Optional[str] = None
    doubanId: Optional[str] = None

    class Config:
        populate_by_name = True


class ControlAnimeCreateRequest(BaseModel):
    """用于外部API自定义创建影视条目的请求模型"""
    title: str = Field(..., description="作品主标题")
    type: AutoImportMediaType = Field(..., description="媒体类型")
    season: Optional[int] = Field(None, description="季度号 (tv_series 类型必需)")
    year: Optional[int] = Field(None, description="年份")
    nameEn: Optional[str] = Field(None, description="英文标题")
    nameJp: Optional[str] = Field(None, description="日文标题")
    nameRomaji: Optional[str] = Field(None, description="罗马音标题")
    aliasCn1: Optional[str] = Field(None, description="中文别名1")
    aliasCn2: Optional[str] = Field(None, description="中文别名2")
    aliasCn3: Optional[str] = Field(None, description="中文别名3")
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    imdbId: Optional[str] = None
    doubanId: Optional[str] = None

    @model_validator(mode='after')
    def check_season_for_tv_series(self):
        if self.type == 'tv_series' and self.season is None:
            raise ValueError('对于电视节目 (tv_series)，季度 (season) 是必需的。')
        return self


class ControlEditedImportRequest(BaseModel):
    """编辑后导入请求模型"""
    searchId: str = Field(..., description="来自搜索响应的searchId")
    resultIndex: int = Field(..., alias="result_index", ge=0, description="要编辑的结果的索引 (从0开始)")
    title: Optional[str] = Field(None, description="覆盖原始标题")
    episodes: List[models.ProviderEpisodeInfo] = Field(..., description="编辑后的分集列表")
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    imdbId: Optional[str] = None
    doubanId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = Field(None, description="强制指定TMDB剧集组ID")

    class Config:
        populate_by_name = True


class ControlUrlImportRequest(BaseModel):
    """用于外部API通过URL导入到指定源的请求模型"""
    sourceId: int = Field(..., description="要导入到的目标数据源ID")
    episodeIndex: int = Field(..., alias="episode_index", description="要导入的特定集数", gt=0)
    url: str = Field(..., description="包含弹幕的视频页面的URL")
    title: Optional[str] = Field(None, description="（可选）强制指定分集标题")

    class Config:
        populate_by_name = True


class ControlXmlImportRequest(BaseModel):
    """用于外部API通过XML/文本导入到指定源的请求模型"""
    sourceId: int = Field(..., description="要导入到的目标数据源ID")
    episodeIndex: int = Field(..., alias="episode_index", description="要导入的特定集数", gt=0)
    content: str = Field(..., description="XML或纯文本格式的弹幕内容")
    title: Optional[str] = Field(None, description="（可选）强制指定分集标题")

    class Config:
        populate_by_name = True


class DanmakuOutputSettings(BaseModel):
    """弹幕输出设置模型"""
    limitPerSource: int = Field(..., alias="limit_per_source")
    mergeOutputEnabled: bool = Field(..., alias="merge_output_enabled")

    class Config:
        populate_by_name = True


class ControlAnimeDetailsResponse(BaseModel):
    """用于外部API的番剧详情响应模型"""
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


class ControlAutoImportRequest(BaseModel):
    """自动导入请求模型"""
    searchType: AutoImportSearchType
    searchTerm: str
    season: Optional[int] = None
    episode: Optional[str] = None  # 支持单集(如"1")或多集(如"1,3,5,7,9,11-13")格式
    mediaType: Optional[AutoImportMediaType] = None
    preassignedAnimeId: Optional[int] = None  # 预分配的anime_id（用于匹配后备）


class ControlMetadataSearchResponse(BaseModel):
    """用于外部API的元数据搜索响应模型"""
    results: List[models.MetadataDetailsResponse]


# --- 配置管理相关模型 ---

class ConfigItem(BaseModel):
    """配置项模型"""
    key: str
    value: str
    type: str
    description: str


class ConfigUpdateRequest(BaseModel):
    """配置更新请求模型"""
    key: str
    value: str


class ConfigResponse(BaseModel):
    """配置响应模型"""
    configs: List[ConfigItem]


class HelpResponse(BaseModel):
    """帮助响应模型"""
    available_keys: List[str]
    description: str


# --- 剧集组管理相关模型 ---

class EpisodeGroupSummary(BaseModel):
    """剧集组摘要"""
    groupId: str
    tmdbTvId: int
    episodeCount: int
    groupCount: int
    isLocal: bool
    associatedAnimeIds: List[int] = Field(default_factory=list, description="关联了此剧集组的条目ID列表")


class EpisodeInGroupRequest(BaseModel):
    """创建/更新剧集组时的单集信息"""
    id: int = Field(..., description="分集ID（TMDB episodeId 或合成ID）")
    name: str = Field("", description="分集名称")
    episodeNumber: int = Field(..., description="TMDB 原始集数")
    seasonNumber: int = Field(..., description="TMDB 原始季数")
    order: int = Field(0, description="在组内的排序")


class GroupInGroupRequest(BaseModel):
    """创建/更新剧集组时的分组信息"""
    name: str = Field("", description="分组名称")
    order: int = Field(..., description="分组排序")
    episodes: List[EpisodeInGroupRequest] = Field(..., description="该组下的分集列表")


class EpisodeGroupCreateRequest(BaseModel):
    """创建剧集组请求"""
    tmdbTvId: int = Field(..., description="TMDB TV ID")
    groupId: Optional[str] = Field(None, description="TMDB 原生剧集组ID。不传则自动生成本地剧集组 local-{tmdbTvId}")
    name: str = Field("", description="剧集组名称")
    groups: List[GroupInGroupRequest] = Field(..., description="分组列表")
    animeId: Optional[int] = Field(None, description="关联的条目ID，传入则创建后自动关联")


class EpisodeGroupUpdateRequest(BaseModel):
    """更新剧集组请求"""
    tmdbTvId: int = Field(..., description="TMDB TV ID")
    name: str = Field("", description="剧集组名称")
    groups: List[GroupInGroupRequest] = Field(..., description="分组列表")


class EpisodeGroupAssociateRequest(BaseModel):
    """关联/解关联剧集组与条目"""
    animeId: int = Field(..., description="要关联的条目ID")

