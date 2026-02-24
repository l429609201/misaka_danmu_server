"""
NotificationManager — 渠道动态加载与生命周期管理
参考 MediaServerManager 的多实例管理模式。
"""

import importlib
import logging
import pkgutil
from typing import Callable, Dict, Optional, Any

from src.db import crud
from src.notification.base import BaseNotificationChannel

logger = logging.getLogger(__name__)


class NotificationManager:
    """通知渠道管理器"""

    def __init__(self, session_factory: Callable, notification_service):
        self._session_factory = session_factory
        self.notification_service = notification_service
        self.channels: Dict[int, BaseNotificationChannel] = {}  # channel_id -> instance
        self._channel_classes: Dict[str, type] = {}  # channel_type -> class
        self._discover_channel_classes()

    def _discover_channel_classes(self):
        """自动发现 src/notification/ 下的渠道实现"""
        import src.notification as pkg
        for importer, modname, ispkg in pkgutil.iter_modules(pkg.__path__):
            if modname.startswith("_") or modname == "base":
                continue
            try:
                module = importlib.import_module(f"src.notification.{modname}")
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type)
                            and issubclass(attr, BaseNotificationChannel)
                            and attr is not BaseNotificationChannel
                            and getattr(attr, 'channel_type', '')):
                        self._channel_classes[attr.channel_type] = attr
            except Exception as e:
                logger.error(f"加载通知渠道模块 {modname} 失败: {e}", exc_info=True)

    async def initialize(self):
        """从数据库加载所有启用的渠道实例"""
        async with self._session_factory() as session:
            all_channels = await crud.get_all_notification_channels(session)

        for ch_data in all_channels:
            if ch_data.get("isEnabled"):
                await self._load_channel(ch_data)

        # 汇总输出
        _P = "           - "
        enabled_count = len(self.channels)
        type_count = len(self._channel_classes)
        log_lines = [f"通知渠道已初始化 (可用类型: {type_count}, 已启用实例: {enabled_count})"]
        # 已启用的实例
        for ch_id, ch in self.channels.items():
            log_lines.append(f"{_P}[已启用] {ch.name} (id={ch_id})")
        # 未启用的可用类型
        enabled_types = {ch.channel_type for ch in self.channels.values()}
        for ch_type, cls in self._channel_classes.items():
            if ch_type not in enabled_types:
                log_lines.append(f"{_P}[可用] {cls.display_name}")
        logger.info("\n".join(log_lines))

    async def _load_channel(self, ch_data: dict):
        """加载单个渠道实例"""
        channel_type = ch_data["channelType"]
        channel_id = ch_data["id"]
        cls = self._channel_classes.get(channel_type)
        if not cls:
            logger.warning(f"未知的渠道类型: {channel_type}，跳过渠道 {ch_data['name']}(id={channel_id})")
            return

        config = ch_data.get("config", {})
        # 将 eventsConfig 也放入 config 供渠道内部使用
        config["__events_config"] = ch_data.get("eventsConfig", {})

        try:
            instance = cls(
                channel_id=channel_id,
                name=ch_data["name"],
                config=config,
                notification_service=self.notification_service,
            )
            self.channels[channel_id] = instance
        except Exception as e:
            logger.error(f"创建渠道实例失败: {ch_data['name']} - {e}", exc_info=True)

    async def start_channels(self):
        """启动所有已加载的渠道"""
        for ch_id, channel in self.channels.items():
            try:
                await channel.start()
            except Exception as e:
                logger.error(f"启动渠道失败: {channel.name} (id={ch_id}) - {e}", exc_info=True)

    async def stop_channels(self):
        """停止所有渠道"""
        for ch_id, channel in list(self.channels.items()):
            try:
                await channel.stop()
            except Exception as e:
                logger.error(f"停止渠道失败: {channel.name} (id={ch_id}) - {e}", exc_info=True)

    async def reload_channel(self, channel_id: int):
        """重载单个渠道（配置变更后调用）"""
        # 先停止旧实例
        old = self.channels.pop(channel_id, None)
        if old:
            try:
                await old.stop()
            except Exception:
                pass

        # 从数据库重新读取
        async with self._session_factory() as session:
            ch_data = await crud.get_notification_channel_by_id(session, channel_id)

        if not ch_data or not ch_data.get("isEnabled"):
            return

        await self._load_channel(ch_data)
        new_instance = self.channels.get(channel_id)
        if new_instance:
            try:
                await new_instance.start()
            except Exception as e:
                logger.error(f"重载后启动渠道失败: {e}", exc_info=True)

    async def remove_channel(self, channel_id: int):
        """移除渠道实例"""
        old = self.channels.pop(channel_id, None)
        if old:
            try:
                await old.stop()
            except Exception:
                pass

    def get_channel(self, channel_id: int) -> Optional[BaseNotificationChannel]:
        return self.channels.get(channel_id)

    def get_all_channels(self) -> Dict[int, BaseNotificationChannel]:
        return self.channels

    def get_available_channel_types(self) -> list:
        """返回所有可用的渠道类型及其 Schema"""
        result = []
        for ch_type, cls in self._channel_classes.items():
            result.append({
                "channelType": ch_type,
                "displayName": cls.display_name,
                "configSchema": cls.get_config_schema(),
            })
        return result

    def get_channel_schema(self, channel_type: str) -> Optional[list]:
        cls = self._channel_classes.get(channel_type)
        if cls:
            return cls.get_config_schema()
        return None

