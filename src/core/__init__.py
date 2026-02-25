"""
核心模块 - 纯静态配置、时区、缓存抽象

此模块遵循 MoviePilot 的架构设计原则：
- core 层只包含纯静态配置，不依赖数据库
- 缓存抽象层定义在 core，具体后端实现可依赖 db 层
- 安全相关功能请直接导入: from src.security import ...
- 数据库管理器请直接导入: from src.db import ConfigManager, CacheManager

使用方式:
    from src.core import settings, get_now
    from src.core.config import settings
    from src.core.timezone import get_now
    from src.core.cache import cached, get_cache_backend, init_cache_backend
"""

# 配置相关（纯静态配置，无数据库依赖）
from .config import settings, Settings
from .config_schema import CONFIG_SCHEMA, get_config_schema
from .default_configs import get_default_configs

# 时区相关（只依赖 config，无数据库依赖）
from .timezone import get_now, get_app_timezone, get_timezone_offset_str

# 缓存抽象层
from .cache import (
    AsyncCacheBackend,
    MemoryBackend,
    RedisBackend,
    DatabaseBackend,
    HybridBackend,
    cached,
    get_cache_backend,
    init_cache_backend,
    close_cache_backend,
    create_cache_backend,
)

__all__ = [
    # 配置
    'settings',
    'Settings',
    'CONFIG_SCHEMA',
    'get_config_schema',
    'get_default_configs',
    # 时区
    'get_now',
    'get_app_timezone',
    'get_timezone_offset_str',
    # 缓存
    'AsyncCacheBackend',
    'MemoryBackend',
    'RedisBackend',
    'DatabaseBackend',
    'HybridBackend',
    'cached',
    'get_cache_backend',
    'init_cache_backend',
    'close_cache_backend',
    'create_cache_backend',
]

