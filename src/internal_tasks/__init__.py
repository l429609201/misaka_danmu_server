"""
内置轮询任务模块

包含所有内置的后台轮询任务处理器。
新增任务只需在此目录下创建继承 BasePollingTask 的子类文件，即可被自动发现。
"""
import pkgutil
import importlib
import logging

from .base import BasePollingTask

logger = logging.getLogger("InternalTasks")

# 自动导入本包下所有模块（排除 base），触发 __init_subclass__ 注册
_current_package = __name__
for _importer, _modname, _ispkg in pkgutil.iter_modules(__path__):
    if _modname == "base":
        continue
    try:
        importlib.import_module(f".{_modname}", _current_package)
    except Exception as _e:
        logger.warning(f"自动导入内置任务模块 '{_modname}' 失败: {_e}")

__all__ = ["BasePollingTask"]

