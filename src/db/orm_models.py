from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer,
    String, TEXT, TypeDecorator, UniqueConstraint, DECIMAL, func
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from src.core.timezone import get_now

class NaiveDateTime(TypeDecorator):
    """
    自定义数据库类型，确保无论数据库驱动返回何种datetime对象，
    在应用层面我们得到的都是不带时区信息的（naive）datetime。
    这解决了PostgreSQL驱动返回带时区时间，而MySQL驱动返回不带时区时间的不一致性问题。
    使用 DateTime 而非 TIMESTAMP，避免 MySQL 自动进行时区转换。
    """
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        """在写入数据库时，移除时区信息。"""
        if value is not None and value.tzinfo is not None:
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        """从数据库读取时，移除时区信息。"""
        if value is not None and value.tzinfo is not None:
            return value.replace(tzinfo=None)
        return value

class Base(DeclarativeBase):
    pass

class Anime(Base):
    __tablename__ = "anime"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    type: Mapped[str] = mapped_column(Enum('tv_series', 'movie', 'ova', 'other', name="anime_type"), default='tv_series')
    imageUrl: Mapped[Optional[str]] = mapped_column("image_url", String(512))
    localImagePath: Mapped[Optional[str]] = mapped_column("local_image_path", String(512))
    season: Mapped[int] = mapped_column(Integer, default=1)
    episodeCount: Mapped[Optional[int]] = mapped_column("episode_count", Integer)
    year: Mapped[Optional[int]] = mapped_column("year", Integer)
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime, default=get_now, nullable=False)

    sources: Mapped[List["AnimeSource"]] = relationship(back_populates="anime", cascade="all, delete-orphan")
    metadataRecord: Mapped["AnimeMetadata"] = relationship(back_populates="anime", cascade="all, delete-orphan", uselist=False)
    aliases: Mapped["AnimeAlias"] = relationship(back_populates="anime", cascade="all, delete-orphan", uselist=False)

    __table_args__ = (
        Index('idx_title_fulltext', 'title'),
    )

class AnimeSource(Base):
    __tablename__ = "anime_sources"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    animeId: Mapped[int] = mapped_column("anime_id", ForeignKey("anime.id", ondelete="CASCADE"))
    sourceOrder: Mapped[int] = mapped_column("source_order", Integer)
    providerName: Mapped[str] = mapped_column("provider_name", String(500))
    mediaId: Mapped[str] = mapped_column("media_id", String(255))  # 255 以避免复合索引超过 3072 字节
    isFavorited: Mapped[bool] = mapped_column("is_favorited", Boolean, default=False)
    incrementalRefreshEnabled: Mapped[bool] = mapped_column("incremental_refresh_enabled", Boolean, default=False)
    incrementalRefreshFailures: Mapped[int] = mapped_column("incremental_refresh_failures", Integer, default=0)
    lastRefreshLatestEpisodeAt: Mapped[Optional[datetime]] = mapped_column("last_refresh_latest_episode_at", NaiveDateTime, nullable=True)
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime)

    anime: Mapped["Anime"] = relationship(back_populates="sources")
    episodes: Mapped[List["Episode"]] = relationship(back_populates="source", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('anime_id', 'provider_name', 'media_id', name='idx_anime_provider_media_unique'),
        UniqueConstraint('anime_id', 'source_order', name='idx_anime_source_order_unique'),
    )

class Episode(Base):
    __tablename__ = "episode"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sourceId: Mapped[int] = mapped_column("source_id", ForeignKey("anime_sources.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500))
    episodeIndex: Mapped[int] = mapped_column("episode_index", Integer)
    providerEpisodeId: Mapped[Optional[str]] = mapped_column("provider_episode_id", String(500))
    sourceUrl: Mapped[Optional[str]] = mapped_column("source_url", TEXT)
    danmakuFilePath: Mapped[Optional[str]] = mapped_column("danmaku_file_path", String(1024))
    fetchedAt: Mapped[Optional[datetime]] = mapped_column("fetched_at", NaiveDateTime)
    commentCount: Mapped[int] = mapped_column("comment_count", Integer, default=0)

    source: Mapped["AnimeSource"] = relationship(back_populates="episodes")

    __table_args__ = (UniqueConstraint('source_id', 'episode_index', name='idx_source_episode_unique'),)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(500), unique=True)
    hashedPassword: Mapped[str] = mapped_column("hashed_password", String(500))
    token: Mapped[Optional[str]] = mapped_column(TEXT)
    tokenUpdate: Mapped[Optional[datetime]] = mapped_column("token_update", NaiveDateTime)
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime)

    # 关联会话
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserSession(Base):
    """用户会话表，用于多端登录管理"""
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    userId: Mapped[int] = mapped_column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    jti: Mapped[str] = mapped_column(String(500), unique=True, index=True)  # JWT ID
    ipAddress: Mapped[Optional[str]] = mapped_column("ip_address", String(500))
    userAgent: Mapped[Optional[str]] = mapped_column("user_agent", String(500))
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime, default=get_now)
    lastUsedAt: Mapped[Optional[datetime]] = mapped_column("last_used_at", NaiveDateTime)
    expiresAt: Mapped[Optional[datetime]] = mapped_column("expires_at", NaiveDateTime)
    isRevoked: Mapped[bool] = mapped_column("is_revoked", Boolean, default=False)

    # 关联用户
    user: Mapped["User"] = relationship(back_populates="sessions")


class Scraper(Base):
    __tablename__ = "scrapers"
    providerName: Mapped[str] = mapped_column("provider_name", String(500), primary_key=True)
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    displayOrder: Mapped[int] = mapped_column("display_order", Integer, default=0)
    useProxy: Mapped[bool] = mapped_column("use_proxy", Boolean, default=False)

class MetadataSource(Base):
    __tablename__ = "metadata_sources"
    providerName: Mapped[str] = mapped_column("provider_name", String(500), primary_key=True)
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    isAuxSearchEnabled: Mapped[bool] = mapped_column("is_aux_search_enabled", Boolean, default=True)
    displayOrder: Mapped[int] = mapped_column("display_order", Integer, default=0)
    useProxy: Mapped[bool] = mapped_column("use_proxy", Boolean, default=True)
    isFailoverEnabled: Mapped[bool] = mapped_column("is_failover_enabled", Boolean, default=False)
    logRawResponses: Mapped[bool] = mapped_column("log_raw_responses", Boolean, default=False, nullable=False)

class AnimeMetadata(Base):
    __tablename__ = "anime_metadata"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    animeId: Mapped[int] = mapped_column("anime_id", ForeignKey("anime.id", ondelete="CASCADE"), unique=True)
    tmdbId: Mapped[Optional[str]] = mapped_column("tmdb_id", String(500))
    tmdbEpisodeGroupId: Mapped[Optional[str]] = mapped_column("tmdb_episode_group_id", String(500))
    imdbId: Mapped[Optional[str]] = mapped_column("imdb_id", String(500))
    tvdbId: Mapped[Optional[str]] = mapped_column("tvdb_id", String(500))
    doubanId: Mapped[Optional[str]] = mapped_column("douban_id", String(500))
    bangumiId: Mapped[Optional[str]] = mapped_column("bangumi_id", String(500))

    anime: Mapped["Anime"] = relationship(back_populates="metadataRecord")

class Config(Base):
    __tablename__ = "config"
    configKey: Mapped[str] = mapped_column("config_key", String(500), primary_key=True)
    configValue: Mapped[str] = mapped_column("config_value", TEXT().with_variant(MEDIUMTEXT, "mysql"))
    description: Mapped[Optional[str]] = mapped_column(TEXT)

class CacheData(Base):
    __tablename__ = "cache_data"
    cacheProvider: Mapped[Optional[str]] = mapped_column("cache_provider", String(500))
    cacheKey: Mapped[str] = mapped_column("cache_key", String(500), primary_key=True)
    cacheValue: Mapped[str] = mapped_column("cache_value", TEXT().with_variant(MEDIUMTEXT, "mysql"))
    expiresAt: Mapped[datetime] = mapped_column("expires_at", NaiveDateTime, index=True)

class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(500))
    token: Mapped[str] = mapped_column(String(500), unique=True)
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime)
    expiresAt: Mapped[Optional[datetime]] = mapped_column("expires_at", NaiveDateTime)
    dailyCallLimit: Mapped[int] = mapped_column("daily_call_limit", Integer, default=500, server_default="500", nullable=False)
    dailyCallCount: Mapped[int] = mapped_column("daily_call_count", Integer, default=0, server_default="0", nullable=False)
    lastCallAt: Mapped[Optional[datetime]] = mapped_column("last_call_at", NaiveDateTime)

class TokenAccessLog(Base):
    __tablename__ = "token_access_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tokenId: Mapped[int] = mapped_column("token_id", Integer)
    ipAddress: Mapped[str] = mapped_column("ip_address", String(500))
    userAgent: Mapped[Optional[str]] = mapped_column("user_agent", TEXT)
    accessTime: Mapped[datetime] = mapped_column("access_time", NaiveDateTime)
    status: Mapped[str] = mapped_column(String(500))
    path: Mapped[Optional[str]] = mapped_column(String(512))

    __table_args__ = (Index('idx_token_id_time', 'token_id', 'access_time'),)

class UaRule(Base):
    __tablename__ = "ua_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uaString: Mapped[str] = mapped_column("ua_string", String(500), unique=True)
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime)

class BangumiAuth(Base):
    __tablename__ = "bangumi_auth"
    userId: Mapped[int] = mapped_column("user_id", BigInteger, primary_key=True)
    bangumiUserId: Mapped[Optional[int]] = mapped_column("bangumi_user_id", Integer)
    nickname: Mapped[Optional[str]] = mapped_column(String(500))
    avatarUrl: Mapped[Optional[str]] = mapped_column("avatar_url", String(512))
    accessToken: Mapped[str] = mapped_column("access_token", TEXT)
    refreshToken: Mapped[Optional[str]] = mapped_column("refresh_token", TEXT)
    expiresAt: Mapped[Optional[datetime]] = mapped_column("expires_at", NaiveDateTime)
    authorizedAt: Mapped[Optional[datetime]] = mapped_column("authorized_at", NaiveDateTime)

class OauthState(Base):
    __tablename__ = "oauth_states"
    stateKey: Mapped[str] = mapped_column("state_key", String(500), primary_key=True)
    userId: Mapped[int] = mapped_column("user_id", BigInteger)
    expiresAt: Mapped[datetime] = mapped_column("expires_at", NaiveDateTime, index=True)

class AnimeAlias(Base):
    __tablename__ = "anime_aliases"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    animeId: Mapped[int] = mapped_column("anime_id", ForeignKey("anime.id", ondelete="CASCADE"), unique=True)
    nameEn: Mapped[Optional[str]] = mapped_column("name_en", String(500))
    nameJp: Mapped[Optional[str]] = mapped_column("name_jp", String(500))
    nameRomaji: Mapped[Optional[str]] = mapped_column("name_romaji", String(500))
    aliasCn1: Mapped[Optional[str]] = mapped_column("alias_cn_1", String(500))
    aliasCn2: Mapped[Optional[str]] = mapped_column("alias_cn_2", String(500))
    aliasCn3: Mapped[Optional[str]] = mapped_column("alias_cn_3", String(500))
    aliasLocked: Mapped[bool] = mapped_column("alias_locked", Boolean, default=False, server_default="0")

    anime: Mapped["Anime"] = relationship(back_populates="aliases")

class TmdbEpisodeMapping(Base):
    __tablename__ = "tmdb_episode_mapping"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tmdbTvId: Mapped[int] = mapped_column("tmdb_tv_id", Integer)
    tmdbEpisodeGroupId: Mapped[str] = mapped_column("tmdb_episode_group_id", String(500))
    tmdbEpisodeId: Mapped[int] = mapped_column("tmdb_episode_id", Integer)
    tmdbSeasonNumber: Mapped[int] = mapped_column("tmdb_season_number", Integer)
    tmdbEpisodeNumber: Mapped[int] = mapped_column("tmdb_episode_number", Integer)
    customSeasonNumber: Mapped[int] = mapped_column("custom_season_number", Integer)
    customEpisodeNumber: Mapped[int] = mapped_column("custom_episode_number", Integer)
    absoluteEpisodeNumber: Mapped[int] = mapped_column("absolute_episode_number", Integer)
    episodeName: Mapped[Optional[str]] = mapped_column("episode_name", String(500))

    __table_args__ = (
        UniqueConstraint('tmdb_episode_group_id', 'tmdb_episode_id', name='idx_group_episode_unique'),
        Index('idx_custom_season_episode', 'tmdb_tv_id', 'tmdb_episode_group_id', 'custom_season_number', 'custom_episode_number'),
        Index('idx_absolute_episode', 'tmdb_tv_id', 'tmdb_episode_group_id', 'absolute_episode_number'),
    )

class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    # 修正：将Python属性名从 'id' 改为 'taskId'，以匹配API响应模型，同时保持数据库列名为 'id'
    taskId: Mapped[str] = mapped_column("id", String(500), primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    jobType: Mapped[str] = mapped_column("job_type", String(500))
    cronExpression: Mapped[str] = mapped_column("cron_expression", String(500))
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    taskConfig: Mapped[Optional[str]] = mapped_column("task_config", TEXT, default="{}")  # JSON 格式的任务实例级配置
    lastRunAt: Mapped[Optional[datetime]] = mapped_column("last_run_at", NaiveDateTime)
    nextRunAt: Mapped[Optional[datetime]] = mapped_column("next_run_at", NaiveDateTime)

class WebhookTask(Base):
    __tablename__ = "webhook_tasks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    receptionTime: Mapped[datetime] = mapped_column("reception_time", NaiveDateTime, index=True)
    executeTime: Mapped[datetime] = mapped_column("execute_time", NaiveDateTime, index=True)
    webhookSource: Mapped[str] = mapped_column("webhook_source", String(500))
    status: Mapped[str] = mapped_column(String(500), default="pending", index=True) # pending, processing, failed, submitted
    payload: Mapped[str] = mapped_column(TEXT().with_variant(MEDIUMTEXT, "mysql"))
    uniqueKey: Mapped[str] = mapped_column("unique_key", String(500), unique=True)
    taskTitle: Mapped[str] = mapped_column("task_title", String(500))

    __table_args__ = (Index('idx_status_execute_time', 'status', 'execute_time'),)

class TaskHistory(Base):
    __tablename__ = "task_history"
    # 修正：将Python属性名从 'id' 改为 'taskId'，以匹配Pydantic模型，同时保持数据库列名为 'id'
    taskId: Mapped[str] = mapped_column("id", String(500), primary_key=True)
    scheduledTaskId: Mapped[Optional[str]] = mapped_column("scheduled_task_id", ForeignKey("scheduled_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(500))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[Optional[str]] = mapped_column(TEXT().with_variant(MEDIUMTEXT, "mysql"))
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime)
    updatedAt: Mapped[datetime] = mapped_column("updated_at", NaiveDateTime)
    finishedAt: Mapped[Optional[datetime]] = mapped_column("finished_at", NaiveDateTime)
    uniqueKey: Mapped[Optional[str]] = mapped_column("unique_key", String(500), index=True)
    queueType: Mapped[str] = mapped_column("queue_type", String(500), default="download", server_default="download")
    # 任务恢复相关字段：在提交时保存，用于重启后恢复排队中的任务
    taskType: Mapped[Optional[str]] = mapped_column("task_type", String(500), nullable=True)
    taskParameters: Mapped[Optional[str]] = mapped_column("task_parameters", TEXT().with_variant(MEDIUMTEXT, "mysql"), nullable=True)

    __table_args__ = (Index('idx_task_history_created_at', 'created_at'),)

class TaskStateCache(Base):
    """任务状态缓存表，用于存储正在执行任务的参数，支持服务重启后的任务恢复"""
    __tablename__ = "task_state_cache"
    taskId: Mapped[str] = mapped_column("task_id", String(500), primary_key=True)
    taskType: Mapped[str] = mapped_column("task_type", String(500))  # 任务类型，如 'generic_import', 'match_fallback'
    taskParameters: Mapped[str] = mapped_column("task_parameters", TEXT().with_variant(MEDIUMTEXT, "mysql"))  # JSON格式的任务参数
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime)
    updatedAt: Mapped[datetime] = mapped_column("updated_at", NaiveDateTime)

    __table_args__ = (Index('idx_task_type', 'task_type'),)

class ExternalApiLog(Base):
    __tablename__ = "external_api_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accessTime: Mapped[datetime] = mapped_column("access_time", NaiveDateTime)
    ipAddress: Mapped[str] = mapped_column("ip_address", String(500))
    endpoint: Mapped[str] = mapped_column(String(500))
    statusCode: Mapped[int] = mapped_column("status_code", Integer)
    message: Mapped[Optional[str]] = mapped_column(TEXT().with_variant(MEDIUMTEXT, "mysql"))
    requestHeaders: Mapped[Optional[str]] = mapped_column("request_headers", TEXT().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    requestBody: Mapped[Optional[str]] = mapped_column("request_body", TEXT().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    responseHeaders: Mapped[Optional[str]] = mapped_column("response_headers", TEXT().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    responseBody: Mapped[Optional[str]] = mapped_column("response_body", TEXT().with_variant(MEDIUMTEXT, "mysql"), nullable=True)

class RateLimitState(Base):
    __tablename__ = "rate_limit_state"
    providerName: Mapped[str] = mapped_column("provider_name", String(500), primary_key=True)
    requestCount: Mapped[int] = mapped_column("request_count", Integer, default=0)
    lastResetTime: Mapped[datetime] = mapped_column("last_reset_time", NaiveDateTime)
    checksum: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, default=None)

class TitleRecognition(Base):
    """识别词配置表 - 单记录全量存储"""
    __tablename__ = "title_recognition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(TEXT().with_variant(MEDIUMTEXT, "mysql"))
    created_at: Mapped[datetime] = mapped_column("created_at", NaiveDateTime, default=get_now)
    updated_at: Mapped[datetime] = mapped_column("updated_at", NaiveDateTime, default=get_now, onupdate=get_now)


class MediaServer(Base):
    """媒体服务器配置表"""
    __tablename__ = "media_servers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # BigInteger 以匹配数据库和外键
    name: Mapped[str] = mapped_column(String(500))
    providerName: Mapped[str] = mapped_column("provider_name", String(500))  # emby, jellyfin, plex
    url: Mapped[str] = mapped_column(String(512))
    apiToken: Mapped[str] = mapped_column("api_token", String(512))
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    selectedLibraries: Mapped[Optional[str]] = mapped_column("selected_libraries", TEXT)  # JSON array
    filterRules: Mapped[Optional[str]] = mapped_column("filter_rules", TEXT)  # JSON object
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime, default=get_now)
    updatedAt: Mapped[datetime] = mapped_column("updated_at", NaiveDateTime, default=get_now, onupdate=get_now)

    mediaItems: Mapped[List["MediaItem"]] = relationship(back_populates="server", cascade="all, delete-orphan")


class MediaItem(Base):
    """扫描到的媒体项"""
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    serverId: Mapped[int] = mapped_column("server_id", BigInteger, ForeignKey("media_servers.id", ondelete="CASCADE"))
    mediaId: Mapped[str] = mapped_column("media_id", String(500))  # 媒体服务器中的ID
    libraryId: Mapped[Optional[str]] = mapped_column("library_id", String(500))  # 所属媒体库ID
    title: Mapped[str] = mapped_column(String(500))
    mediaType: Mapped[str] = mapped_column("media_type", Enum('movie', 'tv_series', name="media_item_type"))
    season: Mapped[Optional[int]] = mapped_column(Integer)
    episode: Mapped[Optional[int]] = mapped_column(Integer)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    tmdbId: Mapped[Optional[str]] = mapped_column("tmdb_id", String(500))
    tvdbId: Mapped[Optional[str]] = mapped_column("tvdb_id", String(500))
    imdbId: Mapped[Optional[str]] = mapped_column("imdb_id", String(500))
    posterUrl: Mapped[Optional[str]] = mapped_column("poster_url", String(1024))
    isImported: Mapped[bool] = mapped_column("is_imported", Boolean, default=False)
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime, default=get_now)
    updatedAt: Mapped[datetime] = mapped_column("updated_at", NaiveDateTime, default=get_now, onupdate=get_now)

    server: Mapped["MediaServer"] = relationship(back_populates="mediaItems")

    __table_args__ = (
        UniqueConstraint('server_id', 'media_id', name='idx_server_media_unique'),
        Index('idx_media_type', 'media_type'),
        Index('idx_is_imported', 'is_imported'),
        # 性能优化索引
        Index('idx_server_id', 'server_id'),  # 按服务器过滤
        Index('idx_media_items_created_at', 'created_at'),  # 按创建时间排序
        Index('idx_server_type', 'server_id', 'media_type'),  # 复合索引：服务器+类型
        Index('idx_server_type_title', 'server_id', 'media_type', 'title'),  # 复合索引：电视剧分组
    )


class LocalDanmakuItem(Base):
    """本地扫描的弹幕文件"""
    __tablename__ = "local_danmaku_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    filePath: Mapped[str] = mapped_column("file_path", String(1024))  # .xml文件路径
    title: Mapped[str] = mapped_column(String(512))  # 标题
    mediaType: Mapped[str] = mapped_column("media_type", Enum('movie', 'tv_series', name="local_media_type"))
    season: Mapped[Optional[int]] = mapped_column(Integer)  # 季度
    episode: Mapped[Optional[int]] = mapped_column(Integer)  # 集数
    year: Mapped[Optional[int]] = mapped_column(Integer)  # 年份
    tmdbId: Mapped[Optional[str]] = mapped_column("tmdb_id", String(500))
    tvdbId: Mapped[Optional[str]] = mapped_column("tvdb_id", String(500))
    imdbId: Mapped[Optional[str]] = mapped_column("imdb_id", String(500))
    posterUrl: Mapped[Optional[str]] = mapped_column("poster_url", String(1024))
    nfoPath: Mapped[Optional[str]] = mapped_column("nfo_path", String(1024))  # nfo文件路径
    isImported: Mapped[bool] = mapped_column("is_imported", Boolean, default=False)
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime, default=get_now)
    updatedAt: Mapped[datetime] = mapped_column("updated_at", NaiveDateTime, default=get_now, onupdate=get_now)

    __table_args__ = (
        Index('idx_local_file_path', 'file_path', mysql_length=255),
        Index('idx_local_media_type', 'media_type'),
        Index('idx_local_is_imported', 'is_imported'),
    )


class AIMetricsLog(Base):
    """AI 调用统计日志表"""
    __tablename__ = "ai_metrics_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column("timestamp", NaiveDateTime, default=get_now, index=True)
    method: Mapped[str] = mapped_column(String(100))  # select_best_match, recognize_title 等
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    durationMs: Mapped[int] = mapped_column("duration_ms", Integer, default=0)
    tokensUsed: Mapped[int] = mapped_column("tokens_used", Integer, default=0)
    model: Mapped[str] = mapped_column(String(100))
    error: Mapped[Optional[str]] = mapped_column(TEXT)
    cacheHit: Mapped[bool] = mapped_column("cache_hit", Boolean, default=False)

    __table_args__ = (
        Index('idx_ai_metrics_timestamp', 'timestamp'),
        Index('idx_ai_metrics_method', 'method'),
        Index('idx_ai_metrics_success', 'success'),
    )


class NotificationChannel(Base):
    """通知渠道实例"""
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(500))                                      # 显示名称
    channelType: Mapped[str] = mapped_column("channel_type", String(100))               # 渠道类型: telegram / bark / ...
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    useProxy: Mapped[bool] = mapped_column("use_proxy", Boolean, default=False)        # 是否使用代理
    config: Mapped[Optional[str]] = mapped_column(TEXT, default="{}")                   # JSON - 渠道专属配置
    eventsConfig: Mapped[Optional[str]] = mapped_column("events_config", TEXT, default="{}")  # JSON - 事件订阅
    createdAt: Mapped[datetime] = mapped_column("created_at", NaiveDateTime, default=get_now)
    updatedAt: Mapped[datetime] = mapped_column("updated_at", NaiveDateTime, default=get_now, onupdate=get_now)
