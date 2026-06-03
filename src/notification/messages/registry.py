"""
消息注册表 — 管理 event_type 到消息类的映射

MessageRegistry 负责：
- 注册 event_type → 消息类的映射
- 根据 event_type 和 payload 创建消息对象
- 提供默认兜底消息类型
"""
import logging
from typing import Dict, Optional, Type

from .base import NotificationMessage

logger = logging.getLogger(__name__)


class MessageRegistry:
    """消息注册表 — 单例，管理所有消息类型"""

    def __init__(self):
        self._registry: Dict[str, Type[NotificationMessage]] = {}

    def register(self, event_type: str, msg_class: Type[NotificationMessage]):
        """注册消息类型映射"""
        self._registry[event_type] = msg_class

    def register_many(self, mapping: Dict[str, Type[NotificationMessage]]):
        """批量注册"""
        self._registry.update(mapping)

    def create(self, event_type: str, payload: dict) -> Optional[NotificationMessage]:
        """根据 event_type 创建消息对象

        Returns:
            消息对象，未注册的事件类型返回 None
        """
        cls = self._registry.get(event_type)
        if cls is None:
            logger.debug(f"未注册的消息类型: {event_type}")
            return None
        try:
            return cls(payload=payload)
        except Exception as e:
            logger.error(f"创建消息对象失败 [{event_type}]: {e}", exc_info=True)
            return None

    def has(self, event_type: str) -> bool:
        return event_type in self._registry

    def get_all_types(self) -> list:
        return list(self._registry.keys())
