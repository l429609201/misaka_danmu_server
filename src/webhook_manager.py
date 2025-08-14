import importlib
import inspect
import logging
from pathlib import Path
from typing import Dict, Type, List

import aiomysql

from .config_manager import ConfigManager
from .task_manager import TaskManager
from .scraper_manager import ScraperManager
from .webhook.base import BaseWebhook

logger = logging.getLogger(__name__)

class WebhookManager:
    def __init__(self, pool: aiomysql.Pool, task_manager: TaskManager, scraper_manager: ScraperManager, config_manager: ConfigManager):
        self.pool = pool
        self.task_manager = task_manager
        self.scraper_manager = scraper_manager
        self.config_manager = config_manager
        self._handlers: Dict[str, Type[BaseWebhook]] = {}
        self._load_handlers()

    def _load_handlers(self):
        """动态发现并加载 'webhook' 目录下的所有处理器，使用文件名作为类型。"""
        webhook_dir = Path(__file__).parent / "webhook"
        for file in webhook_dir.glob("*.py"):
            if file.name.startswith("_") or file.name == "base.py":
                continue

            module_name = f".webhook.{file.stem}"
            handler_key = file.stem # e.g., 'emby'
            try:
                module = importlib.import_module(module_name, package="src")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseWebhook) and obj is not BaseWebhook:
                        if handler_key in self._handlers:
                            logger.warning(f"发现重复的 Webhook 处理器键 '{handler_key}'。将被覆盖。")
                        self._handlers[handler_key] = obj
                        logger.info(f"Webhook 处理器 '{handler_key}' (来自 {file.name}) 已加载。")
            except Exception as e:
                logger.error(f"从 {file.name} 加载 Webhook 处理器失败: {e}")

    def get_handler(self, webhook_type: str) -> BaseWebhook:
        handler_class = self._handlers.get(webhook_type)
        if not handler_class:
            raise ValueError(f"未找到类型为 '{webhook_type}' 的 Webhook 处理器")
        return handler_class(self.pool, self.task_manager, self.scraper_manager)

    def get_available_handlers(self) -> List[str]:
        """返回所有成功加载的 webhook 处理器类型（即文件名）的列表。"""
        return list(self._handlers.keys())