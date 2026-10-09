"""通知渠道加载、生命周期与按渠道能力渲染的基础服务。"""

import asyncio
import json
import logging
from typing import Callable, Dict, List, Optional, Any

from src.notification.base import BaseNotificationChannel, ChannelCapability, RenderedMessage
from src.notification.qqbot import QQBotChannel
from src.notification.serverchan import ServerChanChannel
from src.notification.telegram import TelegramChannel
from src.notification.wechat import WeChatChannel
from src.notification.messages.base import NotificationMessage
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


class NotificationManager:
    """通知渠道生命周期与能力渲染，不编排事件业务。"""

    def __init__(self, session_factory: Callable, notification_service):
        self._session_factory = session_factory
        self._db = get_database_service()
        self.notification_service = notification_service
        self.channels: Dict[int, BaseNotificationChannel] = {}  # channel_id -> instance
        self._channel_classes: Dict[str, type] = {
            cls.channel_type: cls
            for cls in (TelegramChannel, QQBotChannel, WeChatChannel, ServerChanChannel)
        }

    async def _get_proxy_url(self) -> str:
        """从数据库读取全局代理 URL（仅 http_socks 模式下有效）"""
        try:
            async with self._db.transaction():
                proxy_mode = await self._db.config.get_value("proxyMode", "none")
                if proxy_mode == "http_socks":
                    return await self._db.config.get_value("proxyUrl", "") or ""
                # 兼容旧配置
                if proxy_mode == "none":
                    proxy_enabled = await self._db.config.get_value("proxyEnabled", "false")
                    if str(proxy_enabled).lower() == "true":
                        return await self._db.config.get_value("proxyUrl", "") or ""
        except Exception as e:
            logger.warning(f"读取代理配置失败: {e}")
        return ""

    async def _get_webhook_api_key(self) -> str:
        """从数据库读取 Webhook API Key"""
        try:
            async with self._db.transaction():
                return await self._db.config.get_value("webhookApiKey", "") or ""
        except Exception as e:
            logger.warning(f"读取 Webhook API Key 失败: {e}")
        return ""

    async def _get_custom_api_domain(self) -> str:
        """从数据库读取「弹幕 → Token 管理 → 自定义域名」。

        why：图片外链模式要把本机图片地址交给第三方平台抓取，需要一个对外可达的
        站点根地址。该地址全站唯一，就是 Token 管理里配置的自定义域名，
        因此在此统一读取并注入各渠道，避免每个渠道各自再配一遍。
        """
        try:
            async with self._db.transaction():
                return await self._db.config.get_value("custom_api_domain", "") or ""
        except Exception as e:
            logger.warning(f"读取自定义域名失败: {e}")
        return ""

    @staticmethod
    def _channel_settings(channel: Any) -> Dict[str, Any]:
        """在事务内提取渠道加载配置，避免将会话绑定对象带入渠道生命周期。"""
        return {
            "id": channel.id,
            "name": channel.name,
            "channelType": channel.channelType,
            "isEnabled": channel.isEnabled,
            "useProxy": channel.useProxy,
            "config": json.loads(channel.config) if channel.config else {},
            "eventsConfig": json.loads(channel.eventsConfig) if channel.eventsConfig else {},
        }

    async def initialize(self) -> None:
        """从数据库加载所有启用的渠道实例"""
        async with self._db.transaction():
            channels = await self._db.notification.get_enabled_channels()
            all_channels = [self._channel_settings(channel) for channel in channels]

        # 预读全局代理 URL、Webhook API Key 和自定义域名
        proxy_url = await self._get_proxy_url()
        webhook_api_key = await self._get_webhook_api_key()
        custom_api_domain = await self._get_custom_api_domain()

        for ch_data in all_channels:
            if ch_data.get("isEnabled"):
                await self._load_channel(
                    ch_data, proxy_url=proxy_url, webhook_api_key=webhook_api_key,
                    custom_api_domain=custom_api_domain,
                )

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

    async def _load_channel(self, ch_data: dict, proxy_url: str = "",
                            webhook_api_key: str = "", custom_api_domain: str = ""):
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
        # 注入自定义域名（图片外链模式据此拼出对外可访问的图片地址）
        if custom_api_domain:
            config["__custom_api_domain"] = custom_api_domain
        else:
            config.pop("__custom_api_domain", None)

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
        async with self._db.transaction():
            channel = await self._db.notification.get_by_id(channel_id)
            ch_data = self._channel_settings(channel) if channel else None

        if not ch_data or not ch_data.get("isEnabled"):
            return

        # 预读全局代理 URL、Webhook API Key 和自定义域名
        proxy_url = await self._get_proxy_url()
        webhook_api_key = await self._get_webhook_api_key()
        custom_api_domain = await self._get_custom_api_domain()
        await self._load_channel(
            ch_data, proxy_url=proxy_url, webhook_api_key=webhook_api_key,
            custom_api_domain=custom_api_domain,
        )
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

    def render_for_channel(self, message: NotificationMessage,
                           channel: BaseNotificationChannel) -> RenderedMessage:
        """按渠道能力选择 Markdown 或纯文本渲染"""
        caps = channel.get_capabilities()
        supports_rich = caps.supports(ChannelCapability.RICH_TEXT)

        prepared = message.payload.get("_rendered_template") if message.payload else None
        if prepared:
            title, body = prepared["title"], prepared["body"]
            if not supports_rich:
                body = message._strip_markdown(body)
            fmt = "markdown" if supports_rich else "text"
        elif supports_rich:
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

