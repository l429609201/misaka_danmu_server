"""
DatabaseService：数据库服务层

全项目**唯一**的数据访问入口：统一管理事务边界与 Repository 实例。
上层（api / tasks / jobs / services / ai）一律经此访问数据库，
禁止直连 crud 层，也禁止自行实例化 Repository。

两种事务模式：

1. 自持 session —— 后台任务、内部调用
   ```python
   db = get_database_service()
   async with db.transaction():
       anime = await db.anime.get_full_details(1)
   ```

2. 接管外部 session —— FastAPI 端点（避免同请求内双事务）
   ```python
   async def endpoint(session: AsyncSession = Depends(db.get_session_dependency)):
       async with db.transaction(session):
           return await db.anime.get_full_details(1)
   ```

数据访问链路：上层 → DatabaseService → Repository → ORM Models
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Optional
from contextvars import ContextVar

import anyio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from fastapi import HTTPException

# ✅ 统一在文件头部导入 ORM 模型
from src.db import orm_models
from src.db.database import get_db_session

# ✅ 改为从 src.db.repositories 导入（新实现）
from src.db.repositories import (
    BangumiDataRepository,
    BangumiDataQueryRepository,
    AnimeRepository,
    AnimeQueryRepository,  # 复杂查询类
    EpisodeRepository,
    EpisodeQueryRepository,  # 复杂查询类
    SourceRepository,
    SourceQueryRepository,  # 复杂查询类
    DanmakuRepository,
    DanmakuQueryRepository,  # 复杂查询类
    TaskRepository,
    TaskQueryRepository,  # 复杂查询类
    ConfigRepository,  # 配置CRUD
    # ── 以下为补齐的单表 CRUD 仓储（原先无服务层入口，上层被迫直连废弃 crud）──
    NotificationRepository,
    NotificationTemplateRepository,
    ApiTokenRepository,
    CacheRepository,
    RateLimitRepository,
    PasskeyRepository,
    SessionRepository,
    ExternalLogRepository,
    TokenLogRepository,
    UaRuleRepository,
    ScraperRepository,
    MetadataSourceRepository,
    AnimeGroupRepository,
    SubscriptionCandidateRepository,
    TitleRecognitionRepository,
    UserRepository,
    OAuthRepository,
    AIMetricsRepository,
    PerformanceRepository,
    PerformanceAlertRepository,
    TmdbRepository,
    ReassociationRepository,
    UtilityRepository,
    LocalDanmakuRepository,
    FallbackRepository,
    DanmakuEditRepository,
    DanmakuStorageRepository,
    MediaServerRepository,
    MediaItemRepository,
    ExternalCalendarRepository,
    WebhookTaskRepository,  # Webhook 任务 CRUD
)
from src.db.repositories.health_query import HealthQueryRepository
from src.db.repositories.config_query import ConfigQueryRepository  # 配置查询类
from src.db.repositories.media_server_query import MediaServerQueryRepository  # 媒体服务器查询类
from src.db.repositories.metadata_source_query import MetadataSourceQueryRepository  # 元数据源查询类
from src.db.repositories.external_calendar_query import ExternalCalendarQueryRepository  # 外部日历查询类
from src.db.repositories.scheduled_task_query import ScheduledTaskQueryRepository  # 定时任务查询类
from src.db.repositories.performance_query import PerformanceQueryRepository  # 性能监测查询类
from src.db.repositories.scraper_query import ScraperQueryRepository  # 爬虫源查询类
from src.db.repositories.local_danmaku import LocalDanmakuQueryRepository  # 本地弹幕查询类
from src.db.repositories.assistant_session import AssistantSessionRepository
from src.db.repositories.llm_query import LLMQueryRepository

logger = logging.getLogger(__name__)

# 【修复】使用 ContextVar 实现协程隔离的 session 和 Repository 缓存
_session_context: ContextVar[Optional[AsyncSession]] = ContextVar('db_session', default=None)
_repo_cache_context: ContextVar[dict] = ContextVar('repo_cache', default=None)


class _RepositoryProxy:
    """
    通用 Repository 代理类

    同时暴露 Repository（CRUD）和 QueryRepository（复杂查询）的方法。
    设计：优先从 QueryRepository 取方法，其次从 Repository 取。
    """

    def __init__(self, repo, query_repo, module_name: str):
        self._repo = repo
        self._query_repo = query_repo
        self._module_name = module_name

    def __getattr__(self, name: str):
        """动态代理：优先 Query 类，其次 Repository"""
        # 优先 Query 类（复杂查询）
        if self._query_repo and hasattr(self._query_repo, name):
            return getattr(self._query_repo, name)
        # 其次 Repository（CRUD）
        elif self._repo and hasattr(self._repo, name):
            return getattr(self._repo, name)
        else:
            raise AttributeError(
                f"'{self._module_name}RepositoryProxy' 没有属性 '{name}'。"
                f"可用方法：{self._module_name}Repository（CRUD）+ {self._module_name}QueryRepository（复杂查询）"
            )


class DatabaseService:
    """
    数据库服务层

    职责：
    1. 统一管理 session 生命周期
    2. 自动事务控制（commit/rollback）
    3. 提供 Repository 访问入口
    4. 组合多个 Repository 完成复杂业务

    使用方式：
    ```python
    db = get_database_service()
    async with db.transaction():
        anime = await db.anime.get_by_id(1)
        episodes = await db.episode.get_by_source(source_id)
    ```
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """
        初始化数据库服务

        Args:
            session_factory: SQLAlchemy 的 async_sessionmaker 实例
        """
        self._session_factory = session_factory
        # 【修复】session 和 Repository 缓存都使用 ContextVar 存储，不再使用实例变量

    class TransactionOutcome:
        """单次自持事务结果；由调用方持有，不放入全局服务共享状态。"""

        def __init__(self) -> None:
            self.status = "not_started"
            self.cancellation: Optional[asyncio.CancelledError] = None

        @property
        def can_restore_files(self) -> bool:
            """仅在未开始事务或提交前已完成回滚时允许文件补偿。"""
            return self.status in {"not_started", "rolled_back"}

        async def settle(self, operation: Awaitable[None]) -> None:
            """等待数据库操作真正结束，暂存重复取消而不取消底层操作。"""
            task = asyncio.ensure_future(operation)
            # 除 asyncio 的单次取消外，还须屏蔽 ASGI/AnyIO 的 level cancellation。
            with anyio.CancelScope(shield=True):
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError as exc:
                        self.cancellation = exc
                    except BaseException:
                        break
                # 以底层结果为准；取消只在全部数据库收尾完成后传播。
                task.result()

    @asynccontextmanager
    async def transaction(
        self, session: Optional[AsyncSession] = None,
        *, outcome: Optional[TransactionOutcome] = None,
    ) -> AsyncIterator["DatabaseService"]:
        """管理事务并等待取消收尾；仍返回服务自身供现有调用方使用。

        自持模式负责提交、回滚、关闭；outcome 可供文件编排判断补偿。
        借用外部 session 时不管理生命周期，也不接受 outcome。
        提交开始后发生底层错误视为结果未知，不能据此恢复旧文件。
        """
        if session is not None:
            if outcome is not None:
                raise ValueError("外部 session 的事务结果由其所有者管理")
            session_token = _session_context.set(session)
            cache_token = _repo_cache_context.set({})
            try:
                yield self
            finally:
                _session_context.reset(session_token)
                _repo_cache_context.reset(cache_token)
            return

        result = outcome if outcome is not None else self.TransactionOutcome()
        if result.status != "not_started":
            raise ValueError("事务结果对象不能重复使用")
        owned_session = self._session_factory()
        session_token = _session_context.set(owned_session)
        cache_token = _repo_cache_context.set({})
        error: Optional[BaseException] = None
        result.status = "active"
        try:
            try:
                yield self
                # 提交一旦启动就等待确定结果，不让请求取消中断驱动提交。
                result.status = "committing"
                await result.settle(owned_session.commit())
                result.status = "committed"
            except BaseException as exc:
                error = exc
                if isinstance(exc, asyncio.CancelledError):
                    result.cancellation = exc
                commit_started = result.status == "committing"
                result.status = "unknown"
                try:
                    await result.settle(owned_session.rollback())
                    if not commit_started:
                        result.status = "rolled_back"
                except BaseException:
                    logger.exception("事务回滚未确认完成，禁止自动恢复关联文件")
                if commit_started:
                    logger.error("数据库提交结果未知，需核查数据库及关联文件", exc_info=True)
                elif isinstance(exc, HTTPException):
                    logger.debug("事务业务异常：%s", exc.detail)
                elif exc.__class__.__name__ == "RateLimitExceededError":
                    # 流控拒绝是预期业务结果，事务回滚正常，不应伪装成数据库故障。
                    logger.debug("事务因流控限制回滚：%s", exc)
                elif not isinstance(exc, asyncio.CancelledError):
                    logger.error("事务执行失败", exc_info=True)
            finally:
                try:
                    # 不依赖 AsyncSession.__aexit__ 的后台关闭，持锁等待真实关闭。
                    await result.settle(owned_session.close())
                except BaseException as exc:
                    logger.exception("数据库会话关闭失败")
                    if error is None:
                        error = exc
        finally:
            _session_context.reset(session_token)
            _repo_cache_context.reset(cache_token)
        if result.cancellation is not None:
            if result.cancellation is error:
                raise error
            raise result.cancellation from error
        if error is not None:
            raise error

    def _reset_repositories(self):
        """
        清空 Repository 实例缓存。

        【已废弃】使用 ContextVar 后不再需要此方法，保留是为了向后兼容。
        """
        pass

    def _get_repo_cache(self) -> dict:
        """获取当前协程的 Repository 缓存"""
        cache = _repo_cache_context.get()
        if cache is None:
            # 如果没有缓存，说明不在 transaction() 上下文中
            raise RuntimeError(
                "DatabaseService 必须在 transaction() 上下文中使用\n"
                "示例: async with db.transaction(): ..."
            )
        return cache

    def _check_session(self):
        """检查当前是否在事务上下文中"""
        # 【修复】从 ContextVar 获取 session
        if _session_context.get() is None:
            raise RuntimeError(
                "DatabaseService 必须在 transaction() 上下文中使用\n"
                "示例: async with db.transaction(): ..."
            )

    @property
    def _session(self) -> Optional[AsyncSession]:
        """获取当前协程的 session（向后兼容属性）"""
        return _session_context.get()

    # ========== Repository 属性（懒加载）==========

    @property
    def bangumi_data(self) -> _RepositoryProxy:
        """获取离线索引写仓储与只读查询仓储。"""
        self._check_session()
        cache = self._get_repo_cache()
        if 'bangumi_data_repo' not in cache:
            cache['bangumi_data_repo'] = BangumiDataRepository(self._session)
        if 'bangumi_data_query_repo' not in cache:
            cache['bangumi_data_query_repo'] = BangumiDataQueryRepository(self._session)
        return _RepositoryProxy(cache['bangumi_data_repo'], cache['bangumi_data_query_repo'], "BangumiData")

    @property
    def anime(self) -> _RepositoryProxy:
        """获取 Anime 数据访问代理（CRUD + 复杂查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'anime_repo' not in cache:
            cache['anime_repo'] = AnimeRepository(self._session)
        if 'anime_query_repo' not in cache:
            cache['anime_query_repo'] = AnimeQueryRepository(self._session)

        return _RepositoryProxy(cache['anime_repo'], cache['anime_query_repo'], "Anime")

    @property
    def episode(self) -> _RepositoryProxy:
        """获取 Episode 数据访问代理（CRUD + 复杂查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'episode_repo' not in cache:
            cache['episode_repo'] = EpisodeRepository(self._session)
        if 'episode_query_repo' not in cache:
            cache['episode_query_repo'] = EpisodeQueryRepository(self._session)

        return _RepositoryProxy(cache['episode_repo'], cache['episode_query_repo'], "Episode")

    @property
    def source(self) -> _RepositoryProxy:
        """获取 Source 数据访问代理（CRUD + 复杂查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'source_repo' not in cache:
            cache['source_repo'] = SourceRepository(self._session)
        if 'source_query_repo' not in cache:
            cache['source_query_repo'] = SourceQueryRepository(self._session)

        return _RepositoryProxy(cache['source_repo'], cache['source_query_repo'], "Source")

    @property
    def danmaku(self) -> _RepositoryProxy:
        """获取 Danmaku 数据访问代理（CRUD + 复杂查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'danmaku_repo' not in cache:
            cache['danmaku_repo'] = DanmakuRepository(self._session)
        if 'danmaku_query_repo' not in cache:
            cache['danmaku_query_repo'] = DanmakuQueryRepository(self._session)

        return _RepositoryProxy(cache['danmaku_repo'], cache['danmaku_query_repo'], "Danmaku")

    @property
    def task(self) -> _RepositoryProxy:
        """获取 Task 数据访问代理（CRUD + 复杂查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'task_repo' not in cache:
            cache['task_repo'] = TaskRepository(self._session)
        if 'task_query_repo' not in cache:
            cache['task_query_repo'] = TaskQueryRepository(self._session)

        return _RepositoryProxy(cache['task_repo'], cache['task_query_repo'], "Task")

    @property
    def config(self) -> _RepositoryProxy:
        """获取 Config 数据访问代理（CRUD + 复杂查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'config_repo' not in cache:
            cache['config_repo'] = ConfigRepository(self._session)
        if 'config_query_repo' not in cache:
            cache['config_query_repo'] = ConfigQueryRepository(self._session)

        return _RepositoryProxy(cache['config_repo'], cache['config_query_repo'], "Config")

    @property
    def media_server(self) -> MediaServerQueryRepository:
        """获取 MediaServer 数据访问代理（仅查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'media_server_query_repo' not in cache:
            cache['media_server_query_repo'] = MediaServerQueryRepository(self._session)

        return cache['media_server_query_repo']

    @property
    def metadata_source(self) -> MetadataSourceQueryRepository:
        """获取 MetadataSource 数据访问代理（仅查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'metadata_source_query_repo' not in cache:
            cache['metadata_source_query_repo'] = MetadataSourceQueryRepository(self._session)

        return cache['metadata_source_query_repo']

    @property
    def external_calendar(self) -> _RepositoryProxy:
        """获取外部日历数据代理，统一暴露查询与订阅写入方法。"""
        self._check_session()
        cache = self._get_repo_cache()

        # 保留查询仓储的同名方法优先级，补齐订阅状态修改等单表操作。
        if 'external_calendar_crud' not in cache:
            cache['external_calendar_crud'] = ExternalCalendarRepository(self._session)
        if 'external_calendar_query_repo' not in cache:
            cache['external_calendar_query_repo'] = ExternalCalendarQueryRepository(self._session)

        return _RepositoryProxy(
            cache['external_calendar_crud'], cache['external_calendar_query_repo'],
            "ExternalCalendar",
        )

    @property
    def scheduled_task(self) -> ScheduledTaskQueryRepository:
        """获取 ScheduledTask 数据访问代理（仅查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'scheduled_task_query_repo' not in cache:
            cache['scheduled_task_query_repo'] = ScheduledTaskQueryRepository(self._session)

        return cache['scheduled_task_query_repo']

    @property
    def performance(self) -> PerformanceQueryRepository:
        """获取 Performance 数据访问代理（仅查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'performance_query_repo' not in cache:
            cache['performance_query_repo'] = PerformanceQueryRepository(self._session)

        return cache['performance_query_repo']

    @property
    def scraper(self) -> ScraperQueryRepository:
        """获取 Scraper 数据访问代理（仅查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'scraper_query_repo' not in cache:
            cache['scraper_query_repo'] = ScraperQueryRepository(self._session)

        return cache['scraper_query_repo']

    @property
    def local_danmaku(self) -> _RepositoryProxy:
        """获取 LocalDanmaku 数据访问代理（CRUD + 复杂查询）"""
        self._check_session()
        cache = self._get_repo_cache()

        if 'local_danmaku_repo' not in cache:
            cache['local_danmaku_repo'] = LocalDanmakuRepository(self._session)
        if 'local_danmaku_query_repo' not in cache:
            cache['local_danmaku_query_repo'] = LocalDanmakuQueryRepository(self._session)

        return _RepositoryProxy(cache['local_danmaku_repo'], cache['local_danmaku_query_repo'], "LocalDanmaku")

    @property
    def health_query(self) -> HealthQueryRepository:
        """在当前事务内提供健康聚合查询仓储。"""
        self._check_session()
        cache = self._get_repo_cache()
        if 'health_query_repo' not in cache:
            cache['health_query_repo'] = HealthQueryRepository(self._session)
        return cache['health_query_repo']

    @property
    def llm_query(self) -> LLMQueryRepository:
        """在当前事务内提供 LLM 只读数据库查询。"""
        self._check_session()
        cache = self._get_repo_cache()
        if "llm_query_repo" not in cache:
            cache["llm_query_repo"] = LLMQueryRepository(self._session)
        return cache["llm_query_repo"]

    # ========== 单表 CRUD 仓储入口（懒加载）==========
    # why: 下列仓储均只有单表 CRUD，无对应 Query 类，逐个写 property 会产生
    #      26 段完全雷同的样板代码。改为「名称 -> 仓储类」映射 + 统一工厂，
    #      既保持 db.<域>.<方法>() 的调用形态，也便于后续新增域。

    #: 单表 CRUD 仓储注册表（属性名 -> 仓储类）
    _SIMPLE_REPOSITORIES = {
        "user": UserRepository,
        "oauth": OAuthRepository,
        "session_store": SessionRepository,
        "passkey": PasskeyRepository,
        "api_token": ApiTokenRepository,
        "token_log": TokenLogRepository,
        "ua_rule": UaRuleRepository,
        "external_log": ExternalLogRepository,
        "notification": NotificationRepository,
        "notification_template": NotificationTemplateRepository,
        "cache": CacheRepository,
        "rate_limit": RateLimitRepository,
        "fallback": FallbackRepository,
        "scraper_crud": ScraperRepository,
        "metadata_source_crud": MetadataSourceRepository,
        "media_server_crud": MediaServerRepository,
        "media_item": MediaItemRepository,
        "external_calendar_crud": ExternalCalendarRepository,
        "performance_crud": PerformanceRepository,
        "performance_alert": PerformanceAlertRepository,
        "anime_group": AnimeGroupRepository,
        "subscription_candidate": SubscriptionCandidateRepository,
        "title_recognition": TitleRecognitionRepository,
        "ai_metrics": AIMetricsRepository,
        "assistant_sessions": AssistantSessionRepository,
        "tmdb": TmdbRepository,
        "reassociation": ReassociationRepository,
        "utility": UtilityRepository,
        "danmaku_edit": DanmakuEditRepository,
        "danmaku_storage": DanmakuStorageRepository,
        "webhook_task": WebhookTaskRepository,  # Webhook 任务 CRUD
    }

    def _get_simple_repo(self, name: str):
        """按注册表懒加载单表 CRUD 仓储，实例在当前事务内复用。"""
        self._check_session()
        cache = self._get_repo_cache()

        if name not in cache:
            cache[name] = self._SIMPLE_REPOSITORIES[name](self._session)

        return cache[name]

    def __getattr__(self, name: str):
        """
        动态暴露单表 CRUD 仓储。

        注意：__getattr__ 仅在常规属性查找失败后触发，因此不会影响上面已显式
        定义的 anime / episode / config 等 property。
        """
        # why: 避免在 __init__ 尚未建立 _simple_repos 时（如反序列化）陷入递归。
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._SIMPLE_REPOSITORIES:
            return self._get_simple_repo(name)
        raise AttributeError(
            f"DatabaseService 没有属性 '{name}'。"
            f"可用数据域：anime / episode / source / danmaku / task / config / "
            f"media_server / metadata_source / external_calendar / scheduled_task / "
            f"performance / scraper，以及 {', '.join(sorted(self._SIMPLE_REPOSITORIES))}"
        )

    # ========== 高层业务方法（组合多个 Repository）==========

    # ========== 缓存操作方法（后备搜索专用）==========

    # ═══════════ 所有手动包装函数已删除 ═══════════
    # 现在通过 _RepositoryProxy 自动发现和调用：
    # - db.cache.store_episode_mapping()
    # - db.cache.get_fallback_search_cache()
    # - db.fallback.get_next_real_anime_id()
    # - db.fallback.ensure_fallback_anime()
    # 等等...

    # ========== 原有高层业务方法 ==========

    async def get_anime_with_sources(self, anime_id: int) -> Optional[dict]:
        """
        获取作品及其所有数据源（跨 Repository 操作）

        Args:
            anime_id: 作品ID

        Returns:
            包含作品和数据源的字典，或 None
        """
        self._check_session()

        anime = await self.anime.get_by_id(anime_id)
        if not anime:
            return None

        # ✅ 已实现：调用 SourceQueryRepository.get_anime_sources()
        sources = await self.source.get_anime_sources(anime_id)

        return {
            "anime": anime,
            "sources": sources,
        }

    async def delete_anime_cascade(self, anime_id: int) -> bool:
        """
        级联删除作品（包括所有关联数据）

        由于 Anime 模型已配置 cascade="all, delete-orphan"，
        删除 Anime 会自动级联删除：
        1. sources（AnimeSource）
        2. episodes（通过 AnimeSource 级联）
        3. comments（通过 Episode 级联）
        4. anime_metadata（AnimeMetadata）
        5. anime_aliases（AnimeAlias）

        Args:
            anime_id: 作品ID

        Returns:
            是否删除成功
        """
        self._check_session()

        # ✅ 直接删除 Anime，ORM 自动级联删除所有关联数据
        result = await self.anime.delete(anime_id)
        return result is not None  # 新方法返回 ORM 对象或 None，转为 bool

    # ========== 服务层统一入口（供 API 层使用）==========

    @staticmethod
    def get_session_dependency():
        """
        提供 FastAPI Depends 用的 session 依赖函数

        ✅ 正确用法（API 层通过服务层获取依赖）：
        ```python
        from src.services.service_container import get_database_service

        db_service = get_database_service()

        @router.post("/xxx")
        async def endpoint(session: AsyncSession = Depends(db_service.get_session_dependency())):
            async with db_service.transaction(session):
                ...
        ```

        ❌ 错误用法（跨层直接导入）：
        ```python
        from src.db import get_db_session  # ❌ API 层跨层访问 DB 层
        ```

        Returns:
            get_db_session 依赖注入函数
        """
        return get_db_session
