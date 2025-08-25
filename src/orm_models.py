from __future__ import annotations
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer,
    String, TEXT, TIMESTAMP, UniqueConstraint, DECIMAL, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Anime(Base):
    __tablename__ = "anime"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    type: Mapped[str] = mapped_column(Enum('tv_series', 'movie', 'ova', 'other', name="anime_type"), default='tv_series')
    imageUrl: Mapped[Optional[str]] = mapped_column("image_url", String(512))
    localImagePath: Mapped[Optional[str]] = mapped_column("local_image_path", String(512))
    season: Mapped[int] = mapped_column(Integer, default=1)
    episodeCount: Mapped[Optional[int]] = mapped_column("episode_count", Integer, nullable=True)
    year: Mapped[Optional[int]] = mapped_column("year", Integer, nullable=True) # type: ignore
    createdAt: Mapped[datetime] = mapped_column("created_at", TIMESTAMP(timezone=True), server_default=func.now(), default=datetime.now)

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
    providerName: Mapped[str] = mapped_column("provider_name", String(50))
    mediaId: Mapped[str] = mapped_column("media_id", String(255))
    isFavorited: Mapped[bool] = mapped_column("is_favorited", Boolean, default=False)
    incrementalRefreshEnabled: Mapped[bool] = mapped_column("incremental_refresh_enabled", Boolean, default=False)
    incrementalRefreshFailures: Mapped[int] = mapped_column("incremental_refresh_failures", Integer, default=0) # type: ignore
    createdAt: Mapped[datetime] = mapped_column("created_at", TIMESTAMP(timezone=True), server_default=func.now(), default=datetime.now)

    anime: Mapped["Anime"] = relationship(back_populates="sources")
    episodes: Mapped[List["Episode"]] = relationship(back_populates="source", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint('anime_id', 'provider_name', 'media_id', name='idx_anime_provider_media_unique'),)

class Episode(Base):
    __tablename__ = "episode"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sourceId: Mapped[int] = mapped_column("source_id", ForeignKey("anime_sources.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    episodeIndex: Mapped[int] = mapped_column("episode_index", Integer)
    providerEpisodeId: Mapped[Optional[str]] = mapped_column("provider_episode_id", String(255))
    sourceUrl: Mapped[Optional[str]] = mapped_column("source_url", String(512)) # type: ignore
    fetchedAt: Mapped[Optional[datetime]] = mapped_column("fetched_at", TIMESTAMP(timezone=True))
    commentCount: Mapped[int] = mapped_column("comment_count", Integer, default=0)

    source: Mapped["AnimeSource"] = relationship(back_populates="episodes")
    comments: Mapped[List["Comment"]] = relationship(back_populates="episode", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint('source_id', 'episode_index', name='idx_source_episode_unique'),)

class Comment(Base):
    __tablename__ = "comment"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cid: Mapped[str] = mapped_column(String(255))
    episodeId: Mapped[int] = mapped_column("episode_id", ForeignKey("episode.id", ondelete="CASCADE"))
    p: Mapped[str] = mapped_column(String(255))
    m: Mapped[str] = mapped_column(TEXT)
    t: Mapped[float] = mapped_column(DECIMAL(10, 2))

    episode: Mapped["Episode"] = relationship(back_populates="comments")

    __table_args__ = (
        UniqueConstraint('episode_id', 'cid', name='idx_episode_cid_unique'),
        Index('idx_episode_time', 'episode_id', 't'),
    )

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    hashedPassword: Mapped[str] = mapped_column("hashed_password", String(255))
    token: Mapped[Optional[str]] = mapped_column(TEXT) # type: ignore
    tokenUpdate: Mapped[Optional[datetime]] = mapped_column("token_update", TIMESTAMP(timezone=True))
    createdAt: Mapped[datetime] = mapped_column("created_at", TIMESTAMP(timezone=True), server_default=func.now(), default=datetime.now)

class Scraper(Base):
    __tablename__ = "scrapers"
    providerName: Mapped[str] = mapped_column("provider_name", String(50), primary_key=True)
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    displayOrder: Mapped[int] = mapped_column("display_order", Integer, default=0)
    useProxy: Mapped[bool] = mapped_column("use_proxy", Boolean, default=False)

class MetadataSource(Base):
    __tablename__ = "metadata_sources"
    providerName: Mapped[str] = mapped_column("provider_name", String(50), primary_key=True)
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    isAuxSearchEnabled: Mapped[bool] = mapped_column("is_aux_search_enabled", Boolean, default=True)
    displayOrder: Mapped[int] = mapped_column("display_order", Integer, default=0)
    useProxy: Mapped[bool] = mapped_column("use_proxy", Boolean, default=False)
    isFailoverEnabled: Mapped[bool] = mapped_column("is_failover_enabled", Boolean, default=False)

class AnimeMetadata(Base):
    __tablename__ = "anime_metadata"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    animeId: Mapped[int] = mapped_column("anime_id", ForeignKey("anime.id", ondelete="CASCADE"), unique=True)
    tmdbId: Mapped[Optional[str]] = mapped_column("tmdb_id", String(50))
    tmdbEpisodeGroupId: Mapped[Optional[str]] = mapped_column("tmdb_episode_group_id", String(50))
    imdbId: Mapped[Optional[str]] = mapped_column("imdb_id", String(50))
    tvdbId: Mapped[Optional[str]] = mapped_column("tvdb_id", String(50))
    doubanId: Mapped[Optional[str]] = mapped_column("douban_id", String(50))
    bangumiId: Mapped[Optional[str]] = mapped_column("bangumi_id", String(50))

    anime: Mapped["Anime"] = relationship(back_populates="metadataRecord")

class Config(Base):
    __tablename__ = "config"
    configKey: Mapped[str] = mapped_column("config_key", String(100), primary_key=True)
    configValue: Mapped[str] = mapped_column("config_value", TEXT)
    description: Mapped[Optional[str]] = mapped_column(TEXT)

class CacheData(Base):
    __tablename__ = "cache_data"
    cacheProvider: Mapped[Optional[str]] = mapped_column("cache_provider", String(50))
    cacheKey: Mapped[str] = mapped_column("cache_key", String(255), primary_key=True) # type: ignore
    cacheValue: Mapped[str] = mapped_column("cache_value", TEXT) # type: ignore
    expiresAt: Mapped[datetime] = mapped_column("expires_at", TIMESTAMP(timezone=True), index=True)

class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    token: Mapped[str] = mapped_column(String(50), unique=True)
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    createdAt: Mapped[datetime] = mapped_column("created_at", TIMESTAMP(timezone=True), server_default=func.now(), default=datetime.now)
    expiresAt: Mapped[Optional[datetime]] = mapped_column("expires_at", TIMESTAMP(timezone=True))

class TokenAccessLog(Base):
    __tablename__ = "token_access_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tokenId: Mapped[int] = mapped_column("token_id", Integer)
    ipAddress: Mapped[str] = mapped_column("ip_address", String(45))
    userAgent: Mapped[Optional[str]] = mapped_column("user_agent", TEXT) # type: ignore
    accessTime: Mapped[datetime] = mapped_column("access_time", TIMESTAMP(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(50))
    path: Mapped[Optional[str]] = mapped_column(String(512)) # type: ignore

    __table_args__ = (Index('idx_token_id_time', 'token_id', 'access_time'),)

class UaRule(Base):
    __tablename__ = "ua_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uaString: Mapped[str] = mapped_column("ua_string", String(255), unique=True)
    createdAt: Mapped[datetime] = mapped_column("created_at", TIMESTAMP(timezone=True), server_default=func.now(), default=datetime.now)

class BangumiAuth(Base):
    __tablename__ = "bangumi_auth"
    userId: Mapped[int] = mapped_column("user_id", BigInteger, primary_key=True)
    bangumiUserId: Mapped[Optional[int]] = mapped_column("bangumi_user_id", Integer)
    nickname: Mapped[Optional[str]] = mapped_column(String(255))
    avatarUrl: Mapped[Optional[str]] = mapped_column("avatar_url", String(512))
    accessToken: Mapped[str] = mapped_column("access_token", TEXT) # type: ignore
    refreshToken: Mapped[Optional[str]] = mapped_column("refresh_token", TEXT) # type: ignore
    expiresAt: Mapped[Optional[datetime]] = mapped_column("expires_at", TIMESTAMP(timezone=True))
    authorizedAt: Mapped[Optional[datetime]] = mapped_column("authorized_at", TIMESTAMP(timezone=True))

class OauthState(Base):
    __tablename__ = "oauth_states"
    stateKey: Mapped[str] = mapped_column("state_key", String(100), primary_key=True) # type: ignore
    userId: Mapped[int] = mapped_column("user_id", BigInteger)
    expiresAt: Mapped[datetime] = mapped_column("expires_at", TIMESTAMP(timezone=True), index=True)

class AnimeAlias(Base):
    __tablename__ = "anime_aliases"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    animeId: Mapped[int] = mapped_column("anime_id", ForeignKey("anime.id", ondelete="CASCADE"), unique=True)
    nameEn: Mapped[Optional[str]] = mapped_column("name_en", String(255))
    nameJp: Mapped[Optional[str]] = mapped_column("name_jp", String(255))
    nameRomaji: Mapped[Optional[str]] = mapped_column("name_romaji", String(255))
    aliasCn1: Mapped[Optional[str]] = mapped_column("alias_cn_1", String(255))
    aliasCn2: Mapped[Optional[str]] = mapped_column("alias_cn_2", String(255))
    aliasCn3: Mapped[Optional[str]] = mapped_column("alias_cn_3", String(255))

    anime: Mapped["Anime"] = relationship(back_populates="aliases")

class TmdbEpisodeMapping(Base):
    __tablename__ = "tmdb_episode_mapping"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tmdbTvId: Mapped[int] = mapped_column("tmdb_tv_id", Integer)
    tmdbEpisodeGroupId: Mapped[str] = mapped_column("tmdb_episode_group_id", String(50))
    tmdbEpisodeId: Mapped[int] = mapped_column("tmdb_episode_id", Integer)
    tmdbSeasonNumber: Mapped[int] = mapped_column("tmdb_season_number", Integer)
    tmdbEpisodeNumber: Mapped[int] = mapped_column("tmdb_episode_number", Integer)
    customSeasonNumber: Mapped[int] = mapped_column("custom_season_number", Integer)
    customEpisodeNumber: Mapped[int] = mapped_column("custom_episode_number", Integer)
    absoluteEpisodeNumber: Mapped[int] = mapped_column("absolute_episode_number", Integer)

    __table_args__ = (
        UniqueConstraint('tmdb_episode_group_id', 'tmdb_episode_id', name='idx_group_episode_unique'),
        Index('idx_custom_season_episode', 'tmdb_tv_id', 'tmdb_episode_group_id', 'custom_season_number', 'custom_episode_number'),
        Index('idx_absolute_episode', 'tmdb_tv_id', 'tmdb_episode_group_id', 'absolute_episode_number'),
    )

class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    # 修正：将Python属性名从 'id' 改为 'taskId'，以匹配API响应模型，同时保持数据库列名为 'id'
    taskId: Mapped[str] = mapped_column("id", String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    jobType: Mapped[str] = mapped_column("job_type", String(50)) # type: ignore
    cronExpression: Mapped[str] = mapped_column("cron_expression", String(100))
    isEnabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True)
    lastRunAt: Mapped[Optional[datetime]] = mapped_column("last_run_at", TIMESTAMP(timezone=True))
    nextRunAt: Mapped[Optional[datetime]] = mapped_column("next_run_at", TIMESTAMP(timezone=True))

class TaskHistory(Base):
    __tablename__ = "task_history"
    # 修正：将Python属性名从 'id' 改为 'taskId'，以匹配Pydantic模型，同时保持数据库列名为 'id'
    taskId: Mapped[str] = mapped_column("id", String(100), primary_key=True)
    scheduledTaskId: Mapped[Optional[str]] = mapped_column("scheduled_task_id", ForeignKey("scheduled_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[Optional[str]] = mapped_column(TEXT) # type: ignore
    createdAt: Mapped[datetime] = mapped_column("created_at", TIMESTAMP(timezone=True), server_default=func.now(), default=datetime.now)
    updatedAt: Mapped[datetime] = mapped_column("updated_at", TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), default=datetime.now)
    finishedAt: Mapped[Optional[datetime]] = mapped_column("finished_at", TIMESTAMP(timezone=True))

    __table_args__ = (Index('idx_created_at', 'created_at'),)

class ExternalApiLog(Base):
    __tablename__ = "external_api_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accessTime: Mapped[datetime] = mapped_column("access_time", TIMESTAMP(timezone=True), server_default=func.now())
    ipAddress: Mapped[str] = mapped_column("ip_address", String(45))
    endpoint: Mapped[str] = mapped_column(String(255))
    statusCode: Mapped[int] = mapped_column("status_code", Integer)
    message: Mapped[Optional[str]] = mapped_column(TEXT) # type: ignore

class RateLimitState(Base):
    __tablename__ = "rate_limit_state"
    providerName: Mapped[str] = mapped_column("provider_name", String(50), primary_key=True)
    requestCount: Mapped[int] = mapped_column("request_count", Integer, default=0)
    lastResetTime: Mapped[datetime] = mapped_column("last_reset_time", TIMESTAMP(timezone=True), server_default=func.now())
