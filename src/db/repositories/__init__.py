"""
Repository 层 - 数据访问抽象

提供统一的数据访问接口，返回 ORM 对象。
数据转换逻辑由 Service 层负责。

命名规范：
- 文件名：<模块名>.py（如 notification.py, config.py）
- 类名：<模块名>Repository（如 NotificationRepository, ConfigRepository）
- 查询类：<模块名>QueryRepository（如 AnimeQueryRepository，处理多表JOIN/聚合/搜索）

架构约定（方案B：分离 Query 类）：
- Repository：单表 CRUD，写操作为主，返回 ORM 对象
- QueryRepository：复杂读查询（多表 JOIN/聚合/搜索），返回字典/列表
- Service：业务编排，组合多个 Repository/QueryRepository，处理事务和外部调用
"""

from .base import BaseRepository
from .bangumi_data import BangumiDataRepository
from .bangumi_data_query import BangumiDataQueryRepository

# 第一批：简单模块（已完成 14/14）
from .notification import NotificationRepository
from .notification_template import NotificationTemplateRepository
from .config import ConfigRepository
from .api_token import ApiTokenRepository
from .cache import CacheRepository
from .rate_limit import RateLimitRepository
from .passkey import PasskeyRepository
from .session import SessionRepository
from .external_log import ExternalLogRepository
from .token_log import TokenLogRepository
from .ua_rule import UaRuleRepository
from .scraper import ScraperRepository
from .metadata_source import MetadataSourceRepository
from .anime_group import AnimeGroupRepository
from .subscription_candidate import SubscriptionCandidateRepository
from .title_recognition import TitleRecognitionRepository

# 第二批：中等模块（已完成 10/10）
from .user import UserRepository
from .oauth import OAuthRepository
from .ai_metrics import AIMetricsRepository
from .performance import PerformanceRepository, PerformanceAlertRepository
from .tmdb import TmdbRepository
from .reassociation import ReassociationRepository
from .utility import UtilityRepository
from .danmaku import DanmakuRepository
from .local_danmaku import LocalDanmakuRepository
from .fallback import FallbackRepository
from .danmaku_edit import DanmakuEditRepository

# 第三批：复杂模块（已完成 7/7 + 5 Query 类）
from .anime import AnimeRepository
from .anime_query import AnimeQueryRepository  # 复杂查询类
from .episode import EpisodeRepository
from .episode_query import EpisodeQueryRepository  # 复杂查询类
from .source import SourceRepository
from .source_query import SourceQueryRepository  # 复杂查询类
from .task import TaskRepository
from .task_query import TaskQueryRepository  # 复杂查询类
from .danmaku_storage import DanmakuStorageRepository
from .danmaku_query import DanmakuQueryRepository  # 复杂查询类
from .media_server import MediaServerRepository, MediaItemRepository
from .external_calendar import ExternalCalendarRepository
from .webhook_task import WebhookTaskRepository  # Webhook 任务 CRUD

__all__ = [
    "BaseRepository",
    "BangumiDataRepository",
    "BangumiDataQueryRepository",
    # 第一批
    "NotificationRepository",
    "NotificationTemplateRepository",
    "ConfigRepository",
    "ApiTokenRepository",
    "CacheRepository",
    "RateLimitRepository",
    "PasskeyRepository",
    "SessionRepository",
    "ExternalLogRepository",
    "TokenLogRepository",
    "UaRuleRepository",
    "ScraperRepository",
    "MetadataSourceRepository",
    "AnimeGroupRepository",
    "SubscriptionCandidateRepository",
    "TitleRecognitionRepository",
    # 第二批
    "UserRepository",
    "OAuthRepository",
    "AIMetricsRepository",
    "PerformanceRepository",
    "PerformanceAlertRepository",
    "TmdbRepository",
    "ReassociationRepository",
    "UtilityRepository",
    "DanmakuRepository",
    "LocalDanmakuRepository",
    "FallbackRepository",
    "DanmakuEditRepository",
    # 第三批
    "AnimeRepository",
    "AnimeQueryRepository",  # Query 类
    "EpisodeRepository",
    "EpisodeQueryRepository",  # Query 类
    "SourceRepository",
    "SourceQueryRepository",  # Query 类
    "TaskRepository",
    "TaskQueryRepository",  # Query 类
    "MediaServerRepository",
    "MediaItemRepository",
    "DanmakuStorageRepository",
    "DanmakuQueryRepository",  # Query 类
    "ExternalCalendarRepository",
    "WebhookTaskRepository",  # Webhook 任务 CRUD
]
