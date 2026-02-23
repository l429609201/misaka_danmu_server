import importlib
import inspect
import pkgutil
import logging
from pathlib import Path
from typing import Dict, Type, List

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import ConfigManager
from src.rate_limiter import RateLimiter
from src.webhook.base import BaseWebhook
from .task_manager import TaskManager
from .scraper_manager import ScraperManager
from .metadata_manager import MetadataSourceManager

logger = logging.getLogger(__name__)

class WebhookManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], task_manager: TaskManager, scraper_manager: ScraperManager, rate_limiter: RateLimiter, metadata_manager: MetadataSourceManager, config_manager: ConfigManager, title_recognition_manager=None, ai_matcher_manager=None):
        self._session_factory = session_factory
        self.task_manager = task_manager
        self.scraper_manager = scraper_manager
        self.rate_limiter = rate_limiter
        self.metadata_manager = metadata_manager
        self.config_manager = config_manager
        self.title_recognition_manager = title_recognition_manager
        self.ai_matcher_manager = ai_matcher_manager
        self.notification_service = None  # 由 main.py 注入
        self._handlers: Dict[str, Type[BaseWebhook]] = {}
        self._load_handlers()

    def _load_handlers(self):
        """动态发现并加载 'webhook' 目录下的所有处理器，使用文件名作为类型。"""
        # 修正：根据运行环境自动调整路径
        def _is_docker_environment():
            """检测是否在Docker容器中运行"""
            import os
            # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
            if Path("/.dockerenv").exists():
                return True
            # 方法2: 检查环境变量
            if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
                return True
            # 方法3: 检查当前工作目录是否为 /app
            if Path.cwd() == Path("/app"):
                return True
            return False

        def _get_webhook_path():
            if _is_docker_environment():
                # 容器环境
                return str(Path("/app/src/webhook"))
            else:
                # 源码运行环境
                return str(Path("src/webhook"))

        webhook_package_path = [_get_webhook_path()]
        for finder, name, ispkg in pkgutil.iter_modules(webhook_package_path):
            if name.startswith("_") or name == "base":
                continue

            handler_key = name  # e.g., 'emby'
            try:
                module_name = f"src.webhook.{name}"
                module = importlib.import_module(module_name)
                for class_name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseWebhook) and obj is not BaseWebhook:
                        if handler_key in self._handlers:
                            logger.warning(f"发现重复的 Webhook 处理器键 '{handler_key}'。将被覆盖。")
                        self._handlers[handler_key] = obj
                        logger.info(f"Webhook 处理器 '{handler_key}' (来自模块 {name}) 已加载。")
            except Exception as e:
                logger.error(f"从模块 {name} 加载 Webhook 处理器失败: {e}")

    def get_handler(self, webhook_type: str) -> BaseWebhook:
        handler_class = self._handlers.get(webhook_type)
        if not handler_class:
            raise ValueError(f"未找到类型为 '{webhook_type}' 的 Webhook 处理器")
        instance = handler_class(self._session_factory, self.task_manager, self.scraper_manager, self.rate_limiter, self.metadata_manager, self.config_manager, self.title_recognition_manager, self.ai_matcher_manager)
        instance.notification_service = self.notification_service
        return instance

    def get_available_handlers(self) -> List[str]:
        """返回所有成功加载的 webhook 处理器类型（即文件名）的列表。"""
        return list(self._handlers.keys())