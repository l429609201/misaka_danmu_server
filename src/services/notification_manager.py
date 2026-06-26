"""
NotificationManager — 渠道动态加载与生命周期管理 + 统一通知出口
参考 MediaServerManager 的多实例管理模式。

C 方案重构后新增职责：
- notify_event / notify_message / reply_message — 统一通知与交互出口
- dispatch — 遍历已启用渠道并发送
- render_for_channel — 按渠道能力选择 Markdown / 纯文本
- 接入 MessageRegistry 和 NotificationAggregator
"""

import asyncio
import importlib
import logging
import pkgutil
from typing import Callable, Dict, List, Optional, Any

from src.db import crud
from src.notification.base import BaseNotificationChannel, ChannelCapability, RenderedMessage
from src.notification.messages.base import NotificationMessage
from src.notification.messages.registry import MessageRegistry
from src.notification.aggregation import NotificationAggregator

logger = logging.getLogger(__name__)


class NotificationManager:
    """通知渠道管理器 + 统一通知出口"""

    def __init__(self, session_factory: Callable, notification_service):
        self._session_factory = session_factory
        self.notification_service = notification_service
        self.channels: Dict[int, BaseNotificationChannel] = {}  # channel_id -> instance
        self._channel_classes: Dict[str, type] = {}  # channel_type -> class
        self._discover_channel_classes()

        # C 方案：消息注册表 & 聚合器
        self._registry = MessageRegistry()
        self._aggregator = NotificationAggregator(time_window=30.0, max_count=10)
        self._flush_task: Optional[asyncio.Task] = None
        self._register_message_types()

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

    def _register_message_types(self):
        """注册所有已知消息类型到注册表"""
        from src.notification.messages.task import (
            ImportMessage, AutoImportMessage, WebhookImportMessage,
            RefreshMessage, IncrementalRefreshMessage,
            ScheduledTaskMessage,
            FallbackDownloadMessage, FallbackSearchMessage,
            PredownloadMessage, MatchFallbackMessage,
        )
        from src.notification.messages.system import (
            SystemStartMessage, WebhookTriggeredMessage,
            MediaScanCompleteMessage, TaskProgressMessage,
        )

        self._registry.register_many({
            # 导入类
            "import_success": ImportMessage,
            "import_failed": ImportMessage,
            "auto_import_success": AutoImportMessage,
            "auto_import_failed": AutoImportMessage,
            "webhook_import_success": WebhookImportMessage,
            "webhook_import_failed": WebhookImportMessage,
            # 刷新类
            "refresh_success": RefreshMessage,
            "refresh_failed": RefreshMessage,
            "incremental_refresh_success": IncrementalRefreshMessage,
            "incremental_refresh_failed": IncrementalRefreshMessage,
            # 定时任务
            "scheduled_task_complete": ScheduledTaskMessage,
            "scheduled_task_failed": ScheduledTaskMessage,
            # 后备任务
            "download_fallback_success": FallbackDownloadMessage,
            "download_fallback_failed": FallbackDownloadMessage,
            "fallback_search_success": FallbackSearchMessage,
            "fallback_search_failed": FallbackSearchMessage,
            "predownload_success": PredownloadMessage,
            "predownload_failed": PredownloadMessage,
            "match_fallback_success": MatchFallbackMessage,
            "match_fallback_failed": MatchFallbackMessage,
            # 系统
            "system_start": SystemStartMessage,
            "webhook_triggered": WebhookTriggeredMessage,
            "media_scan_complete": MediaScanCompleteMessage,
            "task_progress": TaskProgressMessage,
        })
        logger.debug(f"消息注册表初始化完成: {len(self._registry.get_all_types())} 种消息类型")

    async def _get_proxy_url(self) -> str:
        """从数据库读取全局代理 URL（仅 http_socks 模式下有效）"""
        try:
            async with self._session_factory() as session:
                from src.db import crud as _crud
                proxy_mode = await _crud.get_config_value(session, "proxyMode", "none")
                if proxy_mode == "http_socks":
                    return await _crud.get_config_value(session, "proxyUrl", "") or ""
                # 兼容旧配置
                if proxy_mode == "none":
                    proxy_enabled = await _crud.get_config_value(session, "proxyEnabled", "false")
                    if str(proxy_enabled).lower() == "true":
                        return await _crud.get_config_value(session, "proxyUrl", "") or ""
        except Exception as e:
            logger.warning(f"读取代理配置失败: {e}")
        return ""

    async def _get_webhook_api_key(self) -> str:
        """从数据库读取 Webhook API Key"""
        try:
            async with self._session_factory() as session:
                from src.db import crud as _crud
                return await _crud.get_config_value(session, "webhookApiKey", "") or ""
        except Exception as e:
            logger.warning(f"读取 Webhook API Key 失败: {e}")
        return ""

    async def initialize(self):
        """从数据库加载所有启用的渠道实例"""
        async with self._session_factory() as session:
            all_channels = await crud.get_all_notification_channels(session)

        # 预读全局代理 URL 和 Webhook API Key
        proxy_url = await self._get_proxy_url()
        webhook_api_key = await self._get_webhook_api_key()

        for ch_data in all_channels:
            if ch_data.get("isEnabled"):
                await self._load_channel(ch_data, proxy_url=proxy_url, webhook_api_key=webhook_api_key)

        # 汇总输出
        _P = "  - "
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

    async def _load_channel(self, ch_data: dict, proxy_url: str = "", webhook_api_key: str = ""):
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
        # 注入代理配置：若渠道开启了 useProxy 开关且全局代理 URL 有值，则注入
        use_proxy = ch_data.get("useProxy", False)
        if use_proxy and proxy_url:
            config["__proxy_url"] = proxy_url
        else:
            config.pop("__proxy_url", None)
        # 注入 Webhook API Key（渠道注册回调时拼接到 URL）
        if webhook_api_key:
            config["__webhook_api_key"] = webhook_api_key
        else:
            config.pop("__webhook_api_key", None)

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
        # 启动聚合刷新后台任务
        self._flush_task = asyncio.create_task(self._start_flush_loop())

    async def stop_channels(self):
        """停止所有渠道"""
        # 停止聚合刷新任务
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        # 刷新剩余聚合消息
        await self.flush_aggregations()
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

        # 预读全局代理 URL 和 Webhook API Key
        proxy_url = await self._get_proxy_url()
        webhook_api_key = await self._get_webhook_api_key()
        await self._load_channel(ch_data, proxy_url=proxy_url, webhook_api_key=webhook_api_key)
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
                "displayName_en": getattr(cls, "display_name_en", ""),
                "displayName_tw": getattr(cls, "display_name_tw", ""),
                "configSchema": cls.get_config_schema(),
                "hideProxy": getattr(cls, "hide_proxy", False),
            })
        return result

    def get_channel_schema(self, channel_type: str) -> Optional[list]:
        cls = self._channel_classes.get(channel_type)
        if cls:
            return cls.get_config_schema()
        return None

    # ═══════════════════════════════════════════
    # C 方案：统一通知出口
    # ═══════════════════════════════════════════

    async def notify_event(self, event_type: str, payload: dict):
        """业务层最常用入口 — 从事件创建消息对象并分发

        Args:
            event_type: 事件类型标识
            payload: 业务数据字典
        """
        message = self._registry.create(event_type, payload)
        if message is None:
            # 未注册的消息类型，降级为直接发送
            logger.warning(f"未注册的消息类型 [{event_type}]，使用直接发送")
            await self._legacy_send(event_type, payload)
            return

        # 设置 message_type（注册表创建时未设置）
        message.message_type = event_type

        await self.notify_message(message)

    async def notify_message(self, message: NotificationMessage):
        """直接发送消息对象 — 经过聚合后分发"""
        ready_messages = self._aggregator.collect(message)
        for msg in ready_messages:
            await self.dispatch(msg)

    async def reply_message(self, reply: NotificationMessage,
                            target_channel_id: Optional[int] = None):
        """交互回复入口 — 发送到指定渠道"""
        if target_channel_id:
            channel = self.channels.get(target_channel_id)
            if channel:
                rendered = self.render_for_channel(reply, channel)
                await channel.send_rendered(rendered)
        else:
            await self.dispatch(reply)

    async def dispatch(self, message: NotificationMessage):
        """遍历已启用渠道并发送消息

        检查每个渠道的事件订阅配置，只发送给订阅了的渠道。
        """
        for ch_id, channel in self.channels.items():
            try:
                if not self._check_subscription(channel, message):
                    continue
                rendered = self.render_for_channel(message, channel)
                await channel.send_rendered(rendered)
            except Exception as e:
                logger.error(f"渠道 {ch_id} 发送消息 [{message.message_type}] 失败: {e}")

    def render_for_channel(self, message: NotificationMessage,
                           channel: BaseNotificationChannel) -> RenderedMessage:
        """按渠道能力选择 Markdown 或纯文本渲染"""
        caps = channel.get_capabilities()
        supports_rich = caps.supports(ChannelCapability.RICH_TEXT)

        if supports_rich:
            title, body = message.to_markdown()
            fmt = "markdown"
        else:
            title, body = message.to_text()
            fmt = "text"

        return RenderedMessage(
            title=title,
            body=body,
            format=fmt,
            image=message.image(),
            buttons=message.buttons(),
            edit_message_id=message.edit_policy(),
        )

    def render_event_for_channel(self, event_type: str, payload: dict,
                                 channel: BaseNotificationChannel) -> Optional[RenderedMessage]:
        """根据 event_type + payload 为指定渠道生成 RenderedMessage。

        供 notification_service 的进度 edit / 完成消息 edit 路径复用统一消息类，
        避免维护重复的格式化模板。未注册的事件类型返回 None。
        """
        message = self._registry.create(event_type, payload)
        if message is None:
            return None
        message.message_type = event_type
        return self.render_for_channel(message, channel)

    @staticmethod
    def _check_subscription(channel: BaseNotificationChannel,
                            message: NotificationMessage) -> bool:
        """检查渠道是否订阅了此消息类型"""
        events_cfg = channel.config.get("__events_config", {})
        sub_key = message.subscription_key
        if not sub_key:
            return True  # 无订阅 key 的消息默认发送
        return bool(events_cfg.get(sub_key, False))

    async def flush_aggregations(self):
        """手动刷新所有聚合消息"""
        messages = self._aggregator.flush_all()
        for msg in messages:
            await self.dispatch(msg)

    async def _start_flush_loop(self):
        """定时刷新聚合桶的后台任务"""
        while True:
            try:
                await asyncio.sleep(10)
                messages = self._aggregator.flush_expired()
                for msg in messages:
                    await self.dispatch(msg)
                self._aggregator.cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"聚合刷新异常: {e}")

    async def _legacy_send(self, event_type: str, payload: dict):
        """降级发送 — 直接用旧格式发送未注册的消息类型"""
        title = event_type
        text = payload.get("text", "") or payload.get("message", "") or str(payload)
        for ch_id, channel in self.channels.items():
            try:
                events_cfg = channel.config.get("__events_config", {})
                if not events_cfg.get(event_type, False):
                    continue
                await channel.send_message(title=title, text=text)
            except Exception as e:
                logger.error(f"渠道 {ch_id} 降级发送 [{event_type}] 失败: {e}")

