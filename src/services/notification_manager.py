"""
NotificationManager — 渠道动态加载与生命周期管理 + 统一通知出口
参考 MediaServerManager 的多实例管理模式。

新架构职责：
- notify_event_v2 — 通用事件入口（task_event/system_event）
- notify_message / reply_message — 直接消息发送与交互回复
- dispatch — 遍历已启用渠道并发送
- render_for_channel — 按渠道能力选择 Markdown / 纯文本
- 接入 TemplateResolver 和 SubscriptionMatcher
"""

import asyncio
import importlib
import logging
import pkgutil
from typing import Callable, Dict, List, Optional, Any

from src.db import crud
from src.notification.base import BaseNotificationChannel, ChannelCapability, RenderedMessage
from src.notification.messages.base import NotificationMessage
from src.notification.aggregation import NotificationAggregator
# 新增导入
from src.notification.events import (
    EventContext, NotificationEvent, TaskOperation, TaskSource, TaskStatus,
)
from src.notification.template_resolver import TemplateResolver
from src.notification.subscription_matcher import SubscriptionMatcher
from src.notification.messages.unified import UnifiedTaskMessage, UnifiedSystemMessage

logger = logging.getLogger(__name__)


class NotificationManager:
    """通知渠道管理器 + 统一通知出口"""

    def __init__(self, session_factory: Callable, notification_service):
        self._session_factory = session_factory
        self.notification_service = notification_service
        self.channels: Dict[int, BaseNotificationChannel] = {}  # channel_id -> instance
        self._channel_classes: Dict[str, type] = {}  # channel_type -> class
        self._discover_channel_classes()

        # 聚合器（保留用于未来的聚合功能，当前新事件系统不使用）
        self._aggregator = NotificationAggregator(time_window=30.0, max_count=10)
        self._flush_task: Optional[asyncio.Task] = None

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

    async def _get_custom_api_domain(self) -> str:
        """从数据库读取「弹幕 → Token 管理 → 自定义域名」。

        why：图片外链模式要把本机图片地址交给第三方平台抓取，需要一个对外可达的
        站点根地址。该地址全站唯一，就是 Token 管理里配置的自定义域名，
        因此在此统一读取并注入各渠道，避免每个渠道各自再配一遍。
        """
        try:
            async with self._session_factory() as session:
                from src.db import crud as _crud
                return await _crud.get_config_value(session, "custom_api_domain", "") or ""
        except Exception as e:
            logger.warning(f"读取自定义域名失败: {e}")
        return ""

    async def reload_surge_config(self):
        """从数据库读取通知汇总（智能洪峰检测）配置并应用到聚合器。

        供启动初始化和配置变更后调用。读取失败时保留默认值，不影响通知功能。
        """
        try:
            async with self._session_factory() as session:
                enabled_str = await crud.get_config_value(
                    session, "notificationSurgeAggregationEnabled", "true")
                window_str = await crud.get_config_value(
                    session, "notificationSurgeWindowSeconds", "30")
                threshold_str = await crud.get_config_value(
                    session, "notificationSurgeThreshold", "5")
            enabled = str(enabled_str).lower() == "true"
            try:
                window = float(window_str)
            except (ValueError, TypeError):
                window = 30.0
            try:
                threshold = int(threshold_str)
            except (ValueError, TypeError):
                threshold = 5
            self._aggregator.configure_surge(enabled, window, threshold)
            # 汇总桶的时间窗口与洪峰窗口保持一致，确保汇总桶按同样节奏 flush
            self._aggregator._time_window = window
            logger.info(f"通知汇总配置已加载: 启用={enabled}, 窗口={window}s, 阈值={threshold}")
        except Exception as e:
            logger.warning(f"读取通知汇总配置失败，使用默认值: {e}")

    async def initialize(self):
        """从数据库加载所有启用的渠道实例"""
        async with self._session_factory() as session:
            all_channels = await crud.get_all_notification_channels(session)

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

        # 加载通知汇总（智能洪峰检测）配置
        await self.reload_surge_config()

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

    # ═══════════════════════════════════════════
    # C 方案：统一通知出口
    # ═══════════════════════════════════════════

    # 旧事件名 → (操作类型, 触发来源) 的映射。
    # why：messages/registry.py 已随通用事件系统移除，旧的 self._registry 调用会抛
    #      AttributeError 导致所有任务完成通知静默失败。此处把旧事件名翻译成
    #      EventContext，统一转发到 notify_event_v2，避免维护两套发送链路。
    _LEGACY_EVENT_MAP: Dict[str, tuple] = {
        # 弹幕导入类
        "import": (TaskOperation.IMPORT, TaskSource.MANUAL),
        "auto_import": (TaskOperation.IMPORT, TaskSource.AUTO),
        "webhook_import": (TaskOperation.IMPORT, TaskSource.WEBHOOK),
        # 刷新类
        "refresh": (TaskOperation.REFRESH, TaskSource.MANUAL),
        "incremental_refresh": (TaskOperation.INCREMENTAL_REFRESH, TaskSource.AUTO),
        # 后备处理类
        "fallback_search": (TaskOperation.FALLBACK_SEARCH, TaskSource.API),
        "download_fallback": (TaskOperation.FALLBACK_SEARCH, TaskSource.API),
        "predownload": (TaskOperation.FALLBACK_PREDOWNLOAD, TaskSource.API),
        "match_fallback": (TaskOperation.FALLBACK_MATCH, TaskSource.API),
        # 定时任务：无专属模板，归入刷新（定时任务多为刷新/追更类）
        "scheduled_task": (TaskOperation.REFRESH, TaskSource.SCHEDULER),
    }

    # 无 _success/_failed 后缀的特殊事件名 → (操作, 来源, 状态)
    _LEGACY_EVENT_EXACT: Dict[str, tuple] = {
        "media_scan_complete": (TaskOperation.MEDIA_SCAN, TaskSource.MANUAL, TaskStatus.SUCCESS),
        "scheduled_task_complete": (TaskOperation.REFRESH, TaskSource.SCHEDULER, TaskStatus.SUCCESS),
        "scheduled_task_failed": (TaskOperation.REFRESH, TaskSource.SCHEDULER, TaskStatus.FAILED),
    }

    def _build_legacy_event_ctx(self, event_type: str, payload: dict) -> Optional[EventContext]:
        """把旧事件名 + payload 翻译成 EventContext。无法识别时返回 None。"""
        exact = self._LEGACY_EVENT_EXACT.get(event_type)
        if exact:
            operation, source, status = exact
        else:
            # 拆出 xxx_success / xxx_failed 形式
            if event_type.endswith("_success"):
                base, status = event_type[: -len("_success")], TaskStatus.SUCCESS
            elif event_type.endswith("_failed"):
                base, status = event_type[: -len("_failed")], TaskStatus.FAILED
            else:
                return None
            mapped = self._LEGACY_EVENT_MAP.get(base)
            if not mapped:
                return None
            operation, source = mapped

        # subject 承载展示主体，context 承载结果详情（与 UnifiedTaskMessage 约定一致）
        subject = {
            "anime_title": payload.get("anime_title", "") or payload.get("task_title", ""),
            "season": payload.get("season"),
            "episode": payload.get("episode"),
            "episode_count": payload.get("episode_count"),
            "provider": payload.get("provider", "") or payload.get("source", ""),
            "source": payload.get("source", "") or payload.get("provider", ""),
            "media_type": payload.get("media_type", ""),
            "year": payload.get("year"),
            "tmdb_id": payload.get("tmdb_id", ""),
            "media_id": payload.get("media_id", ""),
            "image_url": payload.get("image_url", ""),
        }
        context = {
            "message": payload.get("message", ""),
            "task_title": payload.get("task_title", ""),
            "finished_at": payload.get("finished_at", ""),
            "search_term": payload.get("search_term", ""),
            "search_type": payload.get("search_type", ""),
            "webhook_source": payload.get("webhook_source", ""),
            "unique_key": payload.get("unique_key", ""),
            # 保留原始事件名，便于渠道端做细粒度区分与排查
            "legacy_event_type": event_type,
        }
        return EventContext(
            event_type=NotificationEvent.TASK_EVENT,
            operation=operation,
            source=source,
            status=status,
            subject=subject,
            context=context,
            task_id=payload.get("task_id"),
        )

    async def notify_event(self, event_type: str, payload: dict):
        """业务层最常用入口 — 旧事件名兼容层，内部转发到 notify_event_v2

        Args:
            event_type: 旧事件类型标识（如 refresh_success / import_failed）
            payload: 业务数据字典
        """
        event_ctx = self._build_legacy_event_ctx(event_type, payload)
        if event_ctx is None:
            # 无法映射到通用事件的旧事件，降级为纯文本直发
            logger.warning(f"事件 [{event_type}] 无法映射到通用事件模板，使用降级发送")
            await self._legacy_send(event_type, payload)
            return

        await self.notify_event_v2(event_ctx)

    async def notify_event_v2(self, event_ctx: EventContext):
        """通用事件入口 V2 — 使用 EventContext 处理通用事件

        流程：
        1. 解析事件到模板 ID（TemplateResolver）
        2. 遍历所有已启用渠道
        3. 判断每个渠道的发送范围（SubscriptionMatcher）
        4. 创建统一消息对象（UnifiedTaskMessage/UnifiedSystemMessage）
        5. 发送到匹配的渠道

        Args:
            event_ctx: 事件上下文对象
        """
        # 第一步：解析模板 ID
        template_id = TemplateResolver.resolve(event_ctx)
        if not template_id:
            logger.warning(f"无法解析事件到模板: {event_ctx.to_dict()}")
            return

        # 第二步：创建消息对象
        if event_ctx.event_type == NotificationEvent.TASK_EVENT:
            message = UnifiedTaskMessage(
                payload=event_ctx.to_dict(),
                event_ctx=event_ctx,
            )
        elif event_ctx.event_type == NotificationEvent.SYSTEM_EVENT:
            message = UnifiedSystemMessage(
                payload=event_ctx.to_dict(),
                event_ctx=event_ctx,
            )
        else:
            logger.warning(f"未知事件类型: {event_ctx.event_type}")
            return

        # 设置消息类型为模板 ID
        message.message_type = template_id

        # 第三步：遍历渠道并判断发送范围
        for ch_id, channel in self.channels.items():
            try:
                # 获取渠道的发送范围配置
                events_cfg = channel.config.get("__events_config", {})

                # 新版配置结构：{"version": 2, "scopes": {...}}
                if isinstance(events_cfg, dict) and events_cfg.get("version") == 2:
                    scopes = events_cfg.get("scopes", {})
                else:
                    # 旧版配置或空配置，使用默认范围
                    scopes = SubscriptionMatcher.get_default_scopes()

                # 判断是否应该发送
                should_send = SubscriptionMatcher.should_send(event_ctx, scopes)

                if not should_send:
                    logger.debug(f"渠道 {ch_id} 不订阅此事件: {event_ctx.to_dict()}")
                    continue

                # 渲染并发送
                rendered = self.render_for_channel(message, channel)
                await channel.send_rendered(rendered)

                logger.info(f"渠道 {ch_id} 发送通用事件成功: template={template_id}")

            except Exception as e:
                logger.error(f"渠道 {ch_id} 发送通用事件失败: {e}", exc_info=True)

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
        # 聚合海报（如后备搜索九宫格）：仅生成一次，复用给所有图片渠道，避免重复下载绘制。
        # _collage_cache: None=尚未尝试; False=已尝试但无图; bytes=已生成
        _collage_cache: Any = None
        _collage_tried = False

        for ch_id, channel in self.channels.items():
            try:
                if not self._check_subscription(channel, message):
                    continue
                rendered = self.render_for_channel(message, channel)
                # 仅对支持图片的渠道尝试附加聚合海报（异步，不阻塞业务主流程——
                # 通知本身已在任务完成后异步发出）。失败静默降级为纯文字。
                caps = channel.get_capabilities()
                if caps.supports(ChannelCapability.IMAGES):
                    if not _collage_tried:
                        _collage_tried = True
                        _collage_cache = await self._build_collage_for(message)
                    if _collage_cache:
                        rendered.image_bytes = _collage_cache
                await channel.send_rendered(rendered)
            except Exception as e:
                logger.error(f"渠道 {ch_id} 发送消息 [{message.message_type}] 失败: {e}")

    async def _build_collage_for(self, message: NotificationMessage) -> Optional[bytes]:
        """为消息生成聚合海报（PNG bytes）。受配置开关与代理控制，全程容错返回 None。

        why：海报聚合是可选增强，任何环节失败都不应影响通知发出，故吞掉所有异常。
        """
        try:
            # 读取开关与代理配置（一次 dispatch 仅调用一次）
            enabled = True
            proxy = None
            ssl_verify = True
            try:
                async with self._session_factory() as session:
                    enabled = (await crud.get_config_value(
                        session, "fallbackSearchPosterCollage", "true")).lower() == "true"
                    proxy_enabled = (await crud.get_config_value(
                        session, "proxyEnabled", "false")).lower() == "true"
                    proxy_url = await crud.get_config_value(session, "proxyUrl", "")
                    ssl_verify = (await crud.get_config_value(
                        session, "proxySslVerify", "true")).lower() == "true"
                    proxy = proxy_url if (proxy_enabled and proxy_url) else None
            except Exception:
                pass
            if not enabled:
                return None
            return await message.build_image_bytes(proxy=proxy, ssl_verify=ssl_verify)
        except Exception as e:
            logger.debug(f"生成聚合海报失败（忽略，降级纯文字）: {e}")
            return None

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
        """根据旧事件名 + payload 为指定渠道生成 RenderedMessage。

        供 notification_service 的进度 edit / 完成消息 edit 路径复用统一消息类，
        避免维护重复的格式化模板。无法映射到通用事件模板时返回 None。
        """
        event_ctx = self._build_legacy_event_ctx(event_type, payload)
        if event_ctx is None:
            return None
        template_id = TemplateResolver.resolve(event_ctx)
        if not template_id:
            return None
        message = UnifiedTaskMessage(payload=event_ctx.to_dict(), event_ctx=event_ctx)
        message.message_type = template_id
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

