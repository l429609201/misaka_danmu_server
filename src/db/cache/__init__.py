"""
数据库缓存驱动模块

从 src.core.cache 迁移到此层，解决 core → db 的反向依赖。
"""

from .backend import DatabaseBackend

__all__ = ["DatabaseBackend"]
