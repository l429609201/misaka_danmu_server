"""
数据库层 - 连接、模型、CRUD、管理器

使用方式:
    from src.db import get_db_session, get_db_type
    from src.db import crud, models, orm_models
    from src.db import init_db_tables, close_db_engine, create_initial_admin_user
    from src.db import ConfigManager, CacheManager
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
    create_initial_admin_user,
    DatabaseStartupError,
)

# Pydantic 模型
from . import models

# SQLAlchemy ORM 模型
from . import orm_models
from .orm_models import Base

# 数据库迁移
from .migrations import run_migrations

# 数据库维护
from .db_maintainer import sync_database_schema

# CRUD 操作
from . import crud

# 管理器（依赖 crud，所以放在 crud 之后）
from .config_manager import ConfigManager
from .cache_manager import CacheManager

__all__ = [
    # 数据库连接
    'get_db_session',
    'sync_postgres_sequence',
    'get_db_type',
    'get_db_session_factory',
    '_get_db_url',
    'init_db_tables',
    'close_db_engine',
    'create_initial_admin_user',
    # 模型
    'models',
    'orm_models',
    'Base',
    # 迁移
    'run_migrations',
    # 维护
    'sync_database_schema',
    # CRUD
    'crud',
    # 管理器
    'ConfigManager',
    'CacheManager',
]

