"""
数据库层 - 连接、模型、CRUD、管理器

使用方式:
    from src.db import get_db_session, get_db_type
    , orm_models
    from src.db import init_db_tables, close_db_engine
    # C6: ConfigService 已迁移到 services.config_service.ConfigService
"""

# 数据库连接
from .database import (
    get_db_session,
    sync_postgres_sequence,
    get_db_type,
    get_session_factory as get_db_session_factory,
    _get_db_url,
    init_db_tables,
    close_db_engine,
    DatabaseStartupError,
)

# SQLAlchemy ORM 模型
from . import orm_models
from .orm_models import Base

# 数据库迁移
from .migrations import run_migrations

# 数据库维护
from .db_maintainer import sync_database_schema

# 数据访问层（Repository）
# why：不在此处预导入 crud —— crud 已废弃且在导入时抛 DeprecationWarning，
# 预导入会让每次 `import src.db` 都触发告警，掩盖真实的存量调用点。
# 存量代码仍可通过 `from src.db.crud.<模块> import <函数>` 显式导入。
from . import repositories

# C6: ConfigService 已迁移到 services.config_service.ConfigService

__all__ = [
    # 数据库连接
    'get_db_session',
    'sync_postgres_sequence',
    'get_db_type',
    'get_db_session_factory',
    '_get_db_url',
    'init_db_tables',
    'close_db_engine',
    # 模型
    'orm_models',
    'Base',
    # 迁移
    'run_migrations',
    # 维护
    'sync_database_schema',
    # 数据访问层
    'repositories',
    # C6: ConfigService 已移除，请使用 services.config_service.get_config_service()
]

