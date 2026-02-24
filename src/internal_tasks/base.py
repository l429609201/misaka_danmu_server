"""
内置轮询任务基类

所有内置轮询任务都应继承 BasePollingTask。
子类只需定义类属性和 handler 方法，即可被 InternalPollingManager 自动发现并注册。
"""
import logging
from abc import ABC, abstractmethod
from typing import ClassVar, List, Type

from fastapi import FastAPI

logger = logging.getLogger("InternalTasks")


class BasePollingTask(ABC):
    """
    内置轮询任务基类

    子类定义即自动注册（通过 __init_subclass__）。

    用法::

        class MyTask(BasePollingTask):
            name = "my_task"
            enabled_key = "myTaskEnabled"
            interval_key = "myTaskInterval"
            default_interval = 30
            min_interval = 10
            startup_delay = 60

            @staticmethod
            async def handler(app: FastAPI) -> None:
                ...
    """

    # ---- 子类必须覆盖 ----
    name: ClassVar[str] = ""
    enabled_key: ClassVar[str] = ""
    interval_key: ClassVar[str] = ""

    # ---- 可选覆盖 ----
    default_interval: ClassVar[int] = 15   # 默认轮询间隔（分钟）
    min_interval: ClassVar[int] = 5        # 最小轮询间隔（分钟）
    startup_delay: ClassVar[int] = 60      # 启动延迟（秒）

    # ---- 自动注册表（私有） ----
    _registry: ClassVar[List[Type["BasePollingTask"]]] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # 只注册定义了 name 的具体子类
        if cls.name:
            cls._registry.append(cls)
            logger.debug(f"自动发现内置轮询任务: {cls.name}")

    @staticmethod
    @abstractmethod
    async def handler(app: FastAPI) -> None:
        """任务执行逻辑，由子类实现"""
        ...

    @classmethod
    def get_all_tasks(cls) -> List[Type["BasePollingTask"]]:
        """获取所有已注册的任务类"""
        return list(cls._registry)

