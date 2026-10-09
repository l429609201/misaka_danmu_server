"""通知事件、模板、订阅、图片与发送编辑的完整业务流程。"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from src.notification.base import BaseNotificationChannel, ChannelCapability, RenderedMessage
from src.notification.messages.base import NotificationMessage
from src.notification.aggregation import NotificationAggregator
from src.notification.events import EventContext, NotificationEvent, TaskOperation, TaskSource, TaskStatus
from src.notification.template_resolver import TemplateResolver
from src.notification.subscription_matcher import SubscriptionMatcher
from src.notification.messages.unified import UnifiedTaskMessage, UnifiedSystemMessage
from src.schemas.notification_template import empty_template_variables
from src.services.template_renderer import get_template_renderer
from src.workflows.image_resources import load_image_bytes

logger = logging.getLogger(__name__)


class NotificationWorkflow:
    """通过基础服务端口编排通知，不让 Service 反向调用流程。"""

    def __init__(self, manager, state, database_service) -> None:
        self.manager = manager
        self.state = state
        self._db = database_service
        self._aggregator = NotificationAggregator(time_window=30.0, max_count=10)
        self._flush_task = None

    async def start(self) -> None:
        """加载汇总策略并启动流程自有的刷新协程。"""
        await self.reload_surge_config()
        if self._flush_task is None:
            self._flush_task = asyncio.create_task(self._start_flush_loop())

    async def stop(self) -> None:
        """停止刷新并发送剩余汇总，再由组合根关闭渠道。"""
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        await self.flush_aggregations()

    async def reload_surge_config(self) -> None:
        """读取汇总业务策略，非法配置保留默认值。"""
        try:
            async with self._db.transaction():
                enabled = await self._db.config.get_value("notificationSurgeAggregationEnabled", "true")
                window = await self._db.config.get_value("notificationSurgeWindowSeconds", "30")
                threshold = await self._db.config.get_value("notificationSurgeThreshold", "5")
            try:
                window_value = float(window)
            except (ValueError, TypeError):
                window_value = 30.0
            try:
                threshold_value = int(threshold)
            except (ValueError, TypeError):
                threshold_value = 5
            self._aggregator.configure_surge(str(enabled).lower() == "true", window_value, threshold_value)
            self._aggregator._time_window = window_value
        except Exception as exc:
            logger.warning("读取通知汇总配置失败，使用默认值: %s", exc)

    @property
    def channels(self) -> Dict[int, BaseNotificationChannel]:
        """获取当前渠道快照，重载渠道后无需重建流程。"""
        return self.manager.get_all_channels()

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
            "episode_range": payload.get("episode_range"),
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
        # 统计仅传递真实结构化结果；缺失时由共享变量合同留空，不解析描述伪造计数。
        details = payload.get("context") or payload.get("result") or {}
        if not isinstance(details, dict):
            details = {}
        for key in ("comment_count", "added_count", "success_count", "failed_count", "duration", "error"):
            context[key] = payload.get(key, details.get(key, ""))
        if status == TaskStatus.FAILED and not context.get("error"):
            context["error"] = context.get("message", "")
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

                # 模板读取与渲染在编排层短事务完成，渠道层只负责发送。
                rendered = await self.prepare_rendered(message, channel)
                await channel.send_rendered(rendered)

                logger.info(f"渠道 {ch_id} 发送通用事件成功: template={template_id}")

            except Exception as e:
                logger.error(f"渠道 {ch_id} 发送通用事件失败: {e}", exc_info=True)

    async def prepare_rendered(self, message: NotificationMessage, channel: BaseNotificationChannel) -> RenderedMessage:
        """读取模板后按渠道能力加载原图，图片失败保留文本和现有渠道降级。"""
        await self._prepare_template(message)
        rendered = self.manager.render_for_channel(message, channel)
        if message.image_enabled and rendered.image and channel.get_capabilities().supports(ChannelCapability.IMAGES):
            rendered.image_bytes = await load_image_bytes(rendered.image)
        return rendered

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
                rendered = await self.prepare_rendered(reply, channel)
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
                rendered = await self.prepare_rendered(message, channel)
                # 仅对支持图片的渠道尝试附加聚合海报（异步，不阻塞业务主流程——
                # 通知本身已在任务完成后异步发出）。失败静默降级为纯文字。
                caps = channel.get_capabilities()
                if message.image_enabled and caps.supports(ChannelCapability.IMAGES):
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
                async with self._db.transaction():
                    enabled = (await self._db.config.get_value(
                        "fallbackSearchPosterCollage", "true")).lower() == "true"
                    proxy_enabled = (await self._db.config.get_value(
                        "proxyEnabled", "false")).lower() == "true"
                    proxy_url = await self._db.config.get_value("proxyUrl", "")
                    ssl_verify = (await self._db.config.get_value(
                        "proxySslVerify", "true")).lower() == "true"
                    proxy = proxy_url if (proxy_enabled and proxy_url) else None
            except Exception:
                pass
            if not enabled:
                return None
            return await message.build_image_bytes(proxy=proxy, ssl_verify=ssl_verify)
        except Exception as e:
            logger.debug(f"生成聚合海报失败（忽略，降级纯文字）: {e}")
            return None

    async def _prepare_template(self, message: NotificationMessage) -> None:
        """在编排层短事务读取并渲染数据库模板，失败时保留基类兜底。"""
        template_id = message.message_type
        if not template_id:
            return
        message.payload.pop("_rendered_template", None)
        event_payload = message.payload or {}
        variables = empty_template_variables()
        variables.update(event_payload.get("subject") or {})
        variables.update(event_payload.get("context") or {})
        variables.update({key: value for key, value in event_payload.items()
                         if key not in ("subject", "context", "source", "operation", "status", "event_type")})
        status = event_payload.get("status") or ""
        variables["status_icon"] = {"success": "✅", "failed": "❌", "no_change": "ℹ️"}.get(status, "📋")
        variables["status_name"] = {"success": "成功", "failed": "失败", "no_change": "无变化"}.get(status, "")
        variables["episode_range"] = variables.get("episode_range") or variables.get("episode") or ""
        source_names = {"manual": "手动", "auto": "自动追更", "webhook": "Webhook", "api": "API", "scheduler": "定时任务"}
        variables["trigger_name"] = source_names.get(str(event_payload.get("source") or ""), "")
        operation_names = {"import": "导入", "refresh": "刷新", "incremental_refresh": "自动追更", "fallback_search": "后备处理", "fallback_predownload": "后备处理", "fallback_match": "后备处理", "media_scan": "媒体库扫描"}
        variables["action_name"] = operation_names.get(str(event_payload.get("operation") or ""), "任务")
        try:
            async with self._db.transaction():
                template = await self._db.notification_template.get_by_id(template_id)
            if not template:
                return
            if template.get("imageEnabled") is False:
                message.image_enabled = False
            ok, title, body, error = get_template_renderer().render(
                template.get("title", ""), template.get("body", ""), variables
            )
            if ok:
                message.payload["_rendered_template"] = {"title": title, "body": body}
            else:
                logger.warning("通知模板渲染失败 [%s]: %s", template_id, error)
        except Exception as exc:
            logger.debug("读取通知模板失败 [%s]: %s", template_id, exc)

    async def render_event_for_channel(self, event_type: str, payload: dict,
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
        return await self.prepare_rendered(message, channel)

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


    # 有进度消息记录时，完成通知会 edit 已有消息（适用于所有任务类型）
    _COMPLETE_EVENT_TYPES = {
        "download_fallback_success", "download_fallback_failed",
        "fallback_search_success", "fallback_search_failed",
        "predownload_success", "predownload_failed",
        "match_fallback_success", "match_fallback_failed",
        "import_success", "import_failed",
        "auto_import_success", "auto_import_failed",
        "webhook_import_success", "webhook_import_failed",
        "refresh_success", "refresh_failed",
        "incremental_refresh_success", "incremental_refresh_failed",
        "scheduled_task_complete", "scheduled_task_failed",
    }

    # fallback 完成事件 → 订阅 check_key 的映射（dict）
    _FALLBACK_COMPLETE_EVENTS = {
        "download_fallback_success": "download_fallback_complete",
        "download_fallback_failed": "download_fallback_complete",
        "fallback_search_success": "fallback_search_complete",
        "fallback_search_failed": "fallback_search_complete",
        "predownload_success": "predownload_complete",
        "predownload_failed": "predownload_complete",
        "match_fallback_success": "match_fallback_complete",
        "match_fallback_failed": "match_fallback_complete",
    }

    async def emit_event(self, event_type: str, data: Dict[str, Any]):
        """向所有订阅了该事件的渠道发送通知

        C 方案重构后：优先走 NotificationManager.notify_event 新路径，
        进度消息编辑（task_progress_tg_msg）逻辑保留在此处处理。
        """
        if not self.manager:
            return

        is_any_complete = event_type in self._COMPLETE_EVENT_TYPES
        task_id: str = data.get("task_id", "")

        # 完成事件有进度消息缓存时，需要特殊处理 edit_message
        if is_any_complete and task_id and self.state.get_progress_messages(task_id):
            # 带进度编辑的场景走旧路径（仅影响有缓存进度消息的渠道）
            await self._emit_event_with_progress_edit(event_type, data, task_id)
        else:
            # 常规场景走新路径
            await self.notify_event(event_type, data)

    async def _emit_event_with_progress_edit(self, event_type: str, data: dict, task_id: str):
        """带进度消息编辑的事件发送 — 处理 TG edit_message 场景

        对有缓存进度消息的渠道：edit 已有消息
        对其他渠道：通过新路径正常发送
        """
        channels = self.manager.get_all_channels()
        cached_channels = set(self.state.get_progress_messages(task_id).keys())

        event_ctx = self._build_legacy_event_ctx(event_type, data)
        for ch_id, channel_instance in channels.items():
            try:
                events_cfg = channel_instance.config.get("__events_config", {})
                has_cached_progress = ch_id in cached_channels
                if not has_cached_progress:
                    if event_ctx is None:
                        continue
                    scopes = (
                        events_cfg.get("scopes", {})
                        if isinstance(events_cfg, dict) and events_cfg.get("version") == 2
                        else SubscriptionMatcher.get_default_scopes()
                    )
                    if not SubscriptionMatcher.should_send(event_ctx, scopes):
                        continue

                # 统一使用新消息类渲染（registry + render_for_channel），不再用旧 _format_event_message
                rendered = await self.render_event_for_channel(
                    event_type, data, channel_instance
                )
                if rendered is None:
                    # 未注册的事件类型，跳过（由常规路径兜底）
                    continue
                # why：特殊进度编辑路径也必须复用 send_rendered，不能绕过图片外链处理。
                if has_cached_progress:
                    edit_mid = self.state.get_progress_messages(task_id).get(ch_id)
                    rendered.edit_message_id = edit_mid
                    rendered.metadata["_msg_id_out"] = []
                    await channel_instance.send_rendered(rendered)
                    # 清理该渠道的缓存
                    self.state.remove_progress_message(task_id, ch_id)
                else:
                    await channel_instance.send_rendered(rendered)
            except Exception as e:
                logger.error(f"渠道 {ch_id} 发送事件 {event_type} 失败: {e}")

    async def emit_task_progress(self, task_id: str, task_title: str, progress: int,
                                  description: str, check_event_key: str = "task_progress"):
        """向支持编辑的渠道发送任务进度通知（edit 已有消息）

        常态化实时进度：所有支持 MESSAGE_EDITING 能力的渠道都会收到进度更新，
        不再检查 task_progress 订阅。进度消息通过编辑同一条消息实现，
        不具备编辑能力的渠道（企业微信/Server酱）不会收到进度更新，避免刷屏。

        Args:
            check_event_key: 已废弃，保留参数仅为兼容性（不再使用）
        """
        if not self.manager:
            return
        channels = self.manager.get_all_channels()
        for ch_id, channel_instance in channels.items():
            try:
                # 常态化实时进度：只按渠道能力判断，不检查订阅
                # why：进度消息靠「编辑同一条消息」刷新百分比，只有声明了
                # MESSAGE_EDITING 能力的渠道才支持。不具备该能力的渠道（企业微信/
                # Server酱）若逐条推送进度，会变成刷屏的进度条垃圾消息，
                # 因此这里按能力而非渠道类型判断，新增渠道无需再改这里。
                if not channel_instance.get_capabilities().supports_editing:
                    continue
                # 进度事件没有通用模板；使用普通文本供支持编辑的渠道复用消息。
                percent = max(0, min(100, int(progress)))
                text = f"{task_title}\n[{('█' * (percent // 5)).ljust(20, '░')}] {percent}%\n{description}"
                edit_mid = self.state.get_progress_messages(task_id).get(ch_id)
                msg_id_out: List[int] = []
                logger.debug(f"[进度通知] task_id={task_id[:8]} ch={ch_id} edit_mid={edit_mid} progress={progress}%")
                await channel_instance.send_message(
                    title="", text=text,
                    edit_message_id=edit_mid, _msg_id_out=msg_id_out
                )
                # 记录新发出的 message_id（首次 send 或 edit 失败降级后均更新缓存）
                if msg_id_out:
                    logger.debug(f"[进度通知] task_id={task_id[:8]} 新消息 msg_id={msg_id_out[0]} (edit_mid was {edit_mid})")
                    self.state.set_progress_message(task_id, ch_id, msg_id_out[0])
            except Exception as e:
                logger.debug(f"渠道 {ch_id} 发送任务进度通知失败: {e}")