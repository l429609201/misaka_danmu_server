"""
QQ 官方 Bot 通知渠道实现（WebSocket Gateway）
使用 qq-botpy SDK，通过 WebSocket 接收消息和事件，支持双向交互。
参考：MoviePilot 项目架构 + QQ 官方文档
官方文档：https://bot.q.qq.com/wiki/
"""

import asyncio
import base64
import json
import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import aiohttp

try:
    import botpy
except ImportError:
    botpy = None
try:
    from botpy.types.message import MarkdownPayload
except ImportError:
    MarkdownPayload = None

from src.notification.base import (
    BaseNotificationChannel, CommandResult,
    ChannelCapability, ChannelCapabilities, IMAGE_MODE_FIELD,
    IMAGE_MODE_TEXT, IMAGE_MODE_PUBLIC_URL,
)

logger = logging.getLogger(__name__)
bot_raw_logger = logging.getLogger("bot_raw")


def _get_botpy():
    """获取 QQ Bot SDK，缺少可选依赖时提供安装提示。"""
    if botpy is None:
        raise ImportError("请安装 qq-botpy: pip install qq-botpy")
    return botpy


def _get_markdown_payload():
    """获取 MarkdownPayload，可选依赖缺失时降级为纯文本。"""
    if MarkdownPayload is None:
        logger.warning("无法导入 MarkdownPayload，将使用纯文本格式")
    return MarkdownPayload


class QQBotChannel(BaseNotificationChannel):
    """QQ 官方 Bot 通知渠道 — 基于 WebSocket Gateway 的双向交互"""

    channel_type = "qq"
    display_name = "QQ"
    display_name_en = "QQ"
    display_name_tw = "QQ"

    # QQ Bot 渠道能力配置
    _CAPABILITIES = ChannelCapabilities(
        capabilities={
            ChannelCapability.INLINE_BUTTONS,  # 支持内联按钮（Keyboard）
            ChannelCapability.MENU_COMMANDS,   # 支持菜单命令
            ChannelCapability.RICH_TEXT,       # 支持 Markdown
            ChannelCapability.IMAGES,          # 支持图片
            ChannelCapability.LINKS,           # 支持链接
            ChannelCapability.CALLBACK_QUERIES, # 支持按钮回调
        },
    )

    def __init__(self, channel_id: int, name: str, config: dict, notification_service):
        super().__init__(channel_id, name, config, notification_service)

        self.app_id = config.get("app_id", "")
        self.app_secret = config.get("app_secret", "")

        # 用户 OpenID（单聊）和群组 OpenID（群聊）
        self.user_openid = config.get("user_openid", "")
        self.group_openid = config.get("group_openid", "")

        # 管理员白名单（可选）
        self.admin_whitelist = config.get("admin_whitelist", "")

        # Bot 客户端实例；WebSocket 在主事件循环中以任务形式运行
        # （原先在子线程调用 botpy.run() 会因复用已运行的主循环而抛 "This event loop is already running"，收消息从未生效）
        self._bot_client = None
        self._bot_task: Optional[asyncio.Task] = None
        self._should_stop = False

        # 已处理的事件 ID（botpy RESUME 后可能重放事件，按 ID 去重，容量有限防止无限增长）
        self._seen_event_ids: "OrderedDict[str, None]" = OrderedDict()
        # 被动回复的 msg_seq 计数：同一 msg_id + msg_seq 重复发送会被平台拒绝
        self._reply_seq: "OrderedDict[str, int]" = OrderedDict()

    def _is_log_raw(self) -> bool:
        """检查是否启用原始日志"""
        return str(self.config.get("log_raw", "false")).lower() == "true"

    def _log_raw(self, direction: str, data):
        """记录原始交互日志"""
        if self._is_log_raw():
            bot_raw_logger.info(
                f"[QQ Bot #{self.channel_id}] {direction}\n"
                f"{json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else data}\n"
                f"{'─' * 60}"
            )

    _MAX_TRACKED_IDS = 1000

    def _is_duplicate_event(self, event_id: Optional[str]) -> bool:
        """判断事件是否已处理过（首次出现时登记并返回 False）"""
        if not event_id:
            return False
        if event_id in self._seen_event_ids:
            return True
        self._seen_event_ids[event_id] = None
        while len(self._seen_event_ids) > self._MAX_TRACKED_IDS:
            self._seen_event_ids.popitem(last=False)
        return False

    def _next_reply_seq(self, reply_id: str) -> int:
        """为同一条被动回复目标分配递增的 msg_seq（从 1 开始）"""
        seq = self._reply_seq.pop(reply_id, 0) + 1
        self._reply_seq[reply_id] = seq
        while len(self._reply_seq) > self._MAX_TRACKED_IDS:
            self._reply_seq.popitem(last=False)
        return seq

    def _is_allowed(self, user_openid: Optional[str]) -> bool:
        """管理员白名单校验（未配置白名单时全部放行）"""
        if not self.admin_whitelist:
            return True
        allowed_users = [u.strip() for u in self.admin_whitelist.split(",") if u.strip()]
        if user_openid in allowed_users:
            return True
        logger.warning(f"用户 {user_openid} 不在管理员白名单中，忽略消息")
        return False

    async def _start_bot_client(self):
        """启动 Bot WebSocket 客户端（用于接收消息和事件）"""
        if not self.app_id or not self.app_secret:
            logger.warning("QQ Bot 配置不完整，无法启动消息接收")
            return

        try:
            botpy = _get_botpy()

            # 创建 Bot 客户端
            class MessageBot(botpy.Client):
                def __init__(self, parent_channel: 'QQBotChannel'):
                    # 配置 intents - 指定需要监听的事件类型
                    intents = botpy.Intents(
                        public_messages=True,      # 监听公域消息（群聊）
                        direct_message=True,       # 监听私信
                        interaction=True,          # 监听交互事件（按钮回调）
                    )
                    super().__init__(intents=intents)
                    self.parent_channel = parent_channel

                async def on_ready(self):
                    """Bot 连接成功回调"""
                    logger.info(f"QQ Bot WebSocket 已连接: {self.robot.name}")

                async def on_c2c_message_create(self, message):
                    """处理单聊（C2C）消息"""
                    await self.parent_channel._handle_c2c_message(message)

                async def on_group_at_message_create(self, message):
                    """处理群聊 @机器人 消息"""
                    await self.parent_channel._handle_group_message(message)

                async def on_interaction_create(self, interaction):
                    """处理按钮交互回调"""
                    await self.parent_channel._handle_interaction(interaction)

            self._bot_client = MessageBot(self)
            # 在当前运行中的主循环初始化 botpy 的异步对象，保证 client.loop 与事件分发所在循环一致
            await self._bot_client._async_setup_hook()
            self._bot_task = asyncio.create_task(
                self._run_bot(), name=f"qqbot-ws-{self.channel_id}"
            )
            logger.info("QQ Bot WebSocket 客户端已启动")

        except Exception as e:
            logger.error(f"QQ Bot 启动失败: {e}")

    async def _run_bot(self):
        """运行 botpy WebSocket 主协程；异常时记录日志而不是静默吞掉"""
        try:
            await self._bot_client.start(appid=self.app_id, secret=self.app_secret)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"QQ Bot WebSocket 运行异常: {e}", exc_info=True)

    async def _handle_c2c_message(self, message):
        """处理单聊（C2C）消息"""
        if self._is_duplicate_event(getattr(message, "id", None)):
            return
        try:
            # 记录原始消息
            self._log_raw("⬇️ 收到单聊消息", {
                "message_id": message.id,
                "user_openid": message.author.user_openid,
                "content": message.content,
                "timestamp": message.timestamp if hasattr(message, 'timestamp') else None,
            })

            content = message.content.strip()
            user_openid = message.author.user_openid
            msg_id = message.id

            # 检查管理员权限（如果配置了白名单）
            if not self._is_allowed(user_openid):
                return

            # 判断是命令还是普通消息
            if content.startswith('/'):
                # 解析命令和参数
                parts = content.strip().split(maxsplit=1)
                command = parts[0].lstrip('/') if parts else ""
                args = parts[1] if len(parts) > 1 else ""

                # 处理命令
                result = await self.service.handle_command(
                    command=command,
                    user_id=user_openid,
                    args=args,
                    channel=self,
                )

                # 发送回复
                if result and result.text:
                    await self._send_c2c_message(
                        user_openid=user_openid,
                        content=result.text,
                        keyboard=result.reply_markup if hasattr(result, 'reply_markup') else None,
                        msg_id=msg_id,
                    )
            else:
                # 普通消息：检查是否有活跃对话或触发 LLM Agent
                conv = self.service.get_conversation(user_openid)
                if not conv and await self.service.is_llm_chat_enabled():
                    # 没有活跃对话且 LLM 可用 → 触发 Agent 对话
                    await self._llm_chat_qq(
                        text=content,
                        user_id=user_openid,
                        user_openid=user_openid,
                        reply_msg_id=msg_id,
                        message=message,  # 传递原始消息对象
                    )
                    return

                # 有活跃对话 → 处理文本输入
                result = await self.service.handle_text_input(
                    content, user_openid, self
                )
                if result and result.text:
                    await self._send_c2c_message(
                        user_openid=user_openid,
                        content=result.text,
                        keyboard=result.reply_markup if hasattr(result, 'reply_markup') else None,
                        msg_id=msg_id,
                    )

        except Exception as e:
            logger.error(f"QQ Bot 处理单聊消息失败: {e}", exc_info=True)

    async def _handle_group_message(self, message):
        """处理群聊 @机器人 消息"""
        if self._is_duplicate_event(getattr(message, "id", None)):
            return
        try:
            # 群消息的 author 只有 member_openid（没有 user_openid），白名单与会话都按它判断
            user_openid = getattr(message.author, "member_openid", None)

            # 记录原始消息
            self._log_raw("⬇️ 收到群聊消息", {
                "message_id": message.id,
                "group_openid": message.group_openid,
                "member_openid": user_openid,
                "content": message.content,
                "timestamp": message.timestamp if hasattr(message, 'timestamp') else None,
            })

            content = (message.content or "").strip()
            group_openid = message.group_openid
            msg_id = message.id

            # 检查管理员权限（如果配置了白名单）
            if not self._is_allowed(user_openid):
                return

            # 判断是命令还是普通消息
            if content.startswith('/'):
                # 解析命令和参数
                parts = content.strip().split(maxsplit=1)
                command = parts[0].lstrip('/') if parts else ""
                args = parts[1] if len(parts) > 1 else ""

                # 处理命令
                result = await self.service.handle_command(
                    command=command,
                    user_id=user_openid,
                    args=args,
                    channel=self,
                    group_openid=group_openid,
                )

                # 发送回复
                if result and result.text:
                    await self._send_group_message(
                        group_openid=group_openid,
                        content=result.text,
                        keyboard=result.reply_markup if hasattr(result, 'reply_markup') else None,
                        msg_id=msg_id,
                    )
            else:
                # 普通消息：检查是否有活跃对话或触发 LLM Agent
                conv = self.service.get_conversation(user_openid)
                if not conv and await self.service.is_llm_chat_enabled():
                    # 没有活跃对话且 LLM 可用 → 触发 Agent 对话（群聊版本）
                    await self._llm_chat_qq_group(
                        text=content,
                        user_id=user_openid,
                        group_openid=group_openid,
                        reply_msg_id=msg_id,
                        message=message,  # 传递原始消息对象
                    )
                    return

                # 有活跃对话 → 处理文本输入
                result = await self.service.handle_text_input(
                    content, user_openid, self
                )
                if result and result.text:
                    await self._send_group_message(
                        group_openid=group_openid,
                        content=result.text,
                        keyboard=result.reply_markup if hasattr(result, 'reply_markup') else None,
                        msg_id=msg_id,
                    )

        except Exception as e:
            logger.error(f"QQ Bot 处理群聊消息失败: {e}", exc_info=True)

    async def _handle_interaction(self, interaction):
        """处理按钮交互回调：先 ACK（否则客户端提示操作失败），再交给统一回调分发并回复结果"""
        if self._is_duplicate_event(getattr(interaction, "id", None)):
            return
        resolved = interaction.data.resolved
        callback_data = resolved.button_data or ""
        group_openid = getattr(interaction, "group_openid", None)
        # 单聊用 user_openid，群聊用 group_member_openid
        user_openid = (
            getattr(interaction, "group_member_openid", None) if group_openid
            else getattr(interaction, "user_openid", None)
        ) or resolved.user_id

        self._log_raw("⬇️ 收到按钮回调", {
            "interaction_id": interaction.id,
            "user_openid": user_openid,
            "group_openid": group_openid,
            "callback_data": callback_data,
        })

        allowed = self._is_allowed(user_openid)
        try:
            # code: 0 成功，4 没有权限
            await self._bot_client.api.on_interaction_result(interaction.id, 0 if allowed else 4)
        except Exception as ack_err:
            logger.warning(f"QQ Bot 按钮回调 ACK 失败: {ack_err}")

        if not allowed or not callback_data:
            return

        try:
            result = await self.service.handle_callback(
                callback_data, user_openid, self,
                chat_id=group_openid or user_openid,
                message_id=resolved.message_id,
            )
            text = (result.text if result else None) or (result.answer_callback_text if result else None)
            if not text:
                return
            keyboard = None
            if result.reply_markup:
                keyboard = self._build_keyboard_from_markup(result.reply_markup)
            # 按钮回调没有可引用的 msg_id，使用 event_id 进行被动回复
            event_id = getattr(interaction, "event_id", None)
            if group_openid:
                await self._send_group_message(
                    group_openid=group_openid, content=text, keyboard=keyboard, event_id=event_id,
                )
            else:
                await self._send_c2c_message(
                    user_openid=user_openid, content=text, keyboard=keyboard, event_id=event_id,
                )
        except Exception as e:
            logger.error(f"QQ Bot 处理按钮回调失败: {e}", exc_info=True)

    @staticmethod
    def _is_markdown_denied(err: Exception) -> bool:
        """判断错误是否为“未开通 Markdown 权限 / 不支持 Markdown”（参考 MP：11244、markdown、权限）"""
        msg = str(err)
        return "11244" in msg or "markdown" in msg.lower() or "权限" in msg

    async def _send_to_target(
        self,
        is_group: bool,
        target: str,
        content: Optional[str] = None,
        keyboard: Optional[Dict] = None,
        image_url: Optional[str] = None,
        msg_id: Optional[str] = None,
        markdown: bool = True,
        event_id: Optional[str] = None,
    ) -> bool:
        """单聊/群聊共用的发送实现，返回是否全部发送成功

        - 图片：先上传（/files，返回 dict），再以 msg_type=7 + media 发送
        - 被动回复：图片与文字都带 msg_id/event_id，并按引用递增 msg_seq（同一 msg_id+msg_seq 重复会被拒绝）
        - Markdown 未开通权限时自动回退纯文本（并去掉按钮，按钮依赖 Markdown）
        - botpy 超时会返回 None，视为失败
        """
        kind = "群聊" if is_group else "单聊"
        if not self._bot_client:
            logger.error("Bot 客户端未初始化")
            return False

        api = self._bot_client.api
        if is_group:
            upload = lambda **kw: api.post_group_file(group_openid=target, **kw)
            post = lambda **kw: api.post_group_message(group_openid=target, **kw)
        else:
            upload = lambda **kw: api.post_c2c_file(openid=target, **kw)
            post = lambda **kw: api.post_c2c_message(openid=target, **kw)

        reply_ref = msg_id or event_id

        def with_reply(data: Dict) -> Dict:
            """为被动回复补齐 msg_id/event_id 与递增的 msg_seq"""
            if msg_id:
                data["msg_id"] = msg_id
            elif event_id:
                data["event_id"] = event_id
            if reply_ref:
                data["msg_seq"] = self._next_reply_seq(reply_ref)
            return data

        ok = True
        # 第一步：如果有图片，先上传并发送图片消息
        if image_url:
            try:
                self._log_raw(f"⬆️ 上传{kind}图片", {"target": target, "image_url": image_url})
                media = await upload(file_type=1, url=image_url, srv_send_msg=False)
                # botpy 返回的是 dict，原先用 hasattr 判断恒为 False，图片一直被跳过
                if isinstance(media, dict) and media.get("file_info"):
                    resp = await post(**with_reply({"msg_type": 7, "media": media}))
                    if resp is None:
                        ok = False
                        logger.warning(f"QQ Bot {kind}图片发送无响应（可能超时）: {target}")
                    else:
                        logger.info(f"QQ Bot {kind}图片发送成功: {target}")
                else:
                    ok = False
                    logger.warning(f"QQ Bot {kind}图片上传失败，跳过图片: {image_url} -> {media}")
            except Exception as img_err:
                ok = False
                logger.error(f"QQ Bot {kind}图片发送失败，将只发送文本: {img_err}")

        # 第二步：如果有文本内容，发送文本/Markdown 消息
        if content:
            MarkdownPayload = _get_markdown_payload()
            data: Dict[str, Any] = {"content": content, "msg_type": 0}
            use_md = False
            if markdown and MarkdownPayload:
                try:
                    data["markdown"] = MarkdownPayload(content=content)
                    data["msg_type"] = 2
                    use_md = True
                except Exception as md_err:
                    logger.warning(f"Markdown 创建失败，降级为纯文本: {md_err}")
            if keyboard:
                data["keyboard"] = keyboard

            try:
                self._log_raw(f"⬆️ 发送{kind}消息", {"target": target, "message_data": data})
                try:
                    resp = await post(**with_reply(dict(data)))
                except Exception as send_err:
                    if not (use_md and self._is_markdown_denied(send_err)):
                        raise
                    logger.warning(f"QQ Bot Markdown 发送被拒（{send_err}），回退纯文本")
                    resp = await post(**with_reply({"content": content, "msg_type": 0}))
                if resp is None:
                    ok = False
                    logger.warning(f"QQ Bot {kind}文本发送无响应（可能超时）: {target}")
                else:
                    self._log_raw(f"✅ {kind}消息发送成功", {"target": target})
                    logger.info(f"QQ Bot {kind}文本发送成功: {target}")
            except Exception as e:
                ok = False
                logger.error(f"QQ Bot {kind}消息发送失败: {e}")
        return ok

    async def _send_c2c_message(
        self,
        user_openid: str,
        content: Optional[str] = None,
        keyboard: Optional[Dict] = None,
        image_url: Optional[str] = None,
        msg_id: Optional[str] = None,
        markdown: bool = True,
        event_id: Optional[str] = None,
    ) -> bool:
        """发送单聊消息（通过 botpy API），返回是否成功"""
        return await self._send_to_target(
            False, user_openid, content, keyboard, image_url, msg_id, markdown, event_id,
        )

    async def _send_group_message(
        self,
        group_openid: str,
        content: Optional[str] = None,
        keyboard: Optional[Dict] = None,
        image_url: Optional[str] = None,
        msg_id: Optional[str] = None,
        markdown: bool = True,
        event_id: Optional[str] = None,
    ) -> bool:
        """发送群聊消息（通过 botpy API），返回是否成功"""
        return await self._send_to_target(
            True, group_openid, content, keyboard, image_url, msg_id, markdown, event_id,
        )

    # ========== BaseNotificationChannel 实现 ==========

    async def send_message(self, title: str, text: str, **kwargs):
        """发送消息（BaseNotificationChannel 要求实现）"""
        # 提取参数
        image = kwargs.get("image")
        image_bytes = kwargs.get("image_bytes")
        reply_markup = kwargs.get("reply_markup", [])
        openid = kwargs.get("openid")  # 可选：指定目标 openid
        msg_id = kwargs.get("msg_id")

        # 处理图片
        image_url = None
        if image or image_bytes:
            image_mode = self.image_mode
            if image_mode != IMAGE_MODE_TEXT:
                if image_mode == IMAGE_MODE_PUBLIC_URL:
                    # 外链模式：转换为公网URL
                    image_url = await self.build_public_image_url(image, image_bytes)
                elif image_bytes or (image and (image.startswith("/data/images/") or image.split("?", 1)[0].lower().endswith(".webp"))):
                    # QQ API 仅接收公网图片 URL，复用兼容 JPEG 缩略图，不把本地路径当外链。
                    image_url = await self.build_public_image_url(image or "", image_bytes)
                elif image:
                    image_url = image

        # 构建完整消息文本
        full_text = f"{title}\n\n{text}" if title else text

        # 构建按钮
        keyboard = None
        if reply_markup and self.get_capabilities().supports_buttons:
            keyboard = self._build_keyboard_from_markup(reply_markup)

        # 确定发送目标（优先使用参数指定的 openid）
        target_openid = openid or self.user_openid or self.group_openid
        if not target_openid:
            logger.error("QQ Bot 发送失败：未配置用户 OpenID 或群组 OpenID")
            return False

        # 判断是单聊还是群聊
        is_group = (openid == self.group_openid) if openid else (self.group_openid and not self.user_openid)

        # 发送消息
        if is_group:
            return await self._send_group_message(
                group_openid=target_openid,
                content=full_text,
                keyboard=keyboard,
                image_url=image_url,
                msg_id=msg_id,
            )
        else:
            return await self._send_c2c_message(
                user_openid=target_openid,
                content=full_text,
                keyboard=keyboard,
                image_url=image_url,
                msg_id=msg_id,
            )

    def _build_keyboard_from_markup(self, reply_markup: List[List[Dict]]) -> Dict:
        """从通用按钮格式转换为 QQ Bot 按钮格式"""
        # reply_markup 格式: [[{"text": "按钮1", "callback_data": "action:param"}]]
        rows = []
        # 按钮 id 使用自增序号：原先截取 callback_data 前 20 字符，前缀相同的按钮会撞 id
        btn_seq = 0

        for row_buttons in reply_markup:
            button_row = []
            for btn in row_buttons:
                btn_seq += 1
                button_obj = {
                    "id": str(btn_seq),
                    "render_data": {
                        "label": btn["text"],
                        "visited_label": btn.get("text", btn["text"]),
                    },
                    "action": {
                        "type": 2,  # 回调按钮
                        "permission": {"type": 2},  # 所有人可点击
                        "data": btn.get("callback_data", btn["text"]),
                    }
                }
                button_row.append(button_obj)

                # QQ Bot 每行最多 5 个按钮
                if len(button_row) >= 5:
                    break

            if button_row:
                rows.append({"buttons": button_row})

            # QQ Bot 最多 10 行按钮
            if len(rows) >= 10:
                break

        return {"content": {"rows": rows}}

    async def test_connection(self) -> Dict[str, Any]:
        """测试连接"""
        try:
            # 测试 botpy 是否可用
            _get_botpy()

            # 验证配置完整性
            if not self.app_id or not self.app_secret:
                return {"success": False, "message": "App ID 或 App Secret 未配置"}

            if not self.user_openid and not self.group_openid:
                return {"success": False, "message": "请至少配置一个用户 OpenID 或群组 OpenID"}

            return {
                "success": True,
                "message": f"配置验证成功！Bot 将在启动时连接 WebSocket"
            }
        except ImportError as e:
            return {"success": False, "message": f"请安装 qq-botpy: pip install qq-botpy"}
        except Exception as e:
            return {"success": False, "message": f"连接测试失败: {str(e)}"}

    async def start(self):
        """启动渠道"""
        await super().start()
        # 启动 Bot WebSocket 客户端（主循环中的后台任务）
        await self._start_bot_client()

        # 注册菜单命令
        menu_commands = self.service.get_menu_commands()
        if menu_commands:
            self.register_commands(menu_commands)

        logger.info(f"QQ Bot 渠道已启动: {self.name}")

    def register_commands(self, commands: Dict[str, str]) -> None:
        """注册 QQ Bot 菜单命令

        注意：QQ Bot API 目前不支持通过 API 设置菜单命令，
        需要在 QQ 开放平台后台手动配置。
        这里只是记录日志，便于开发者了解需要配置哪些命令。

        :param commands: {"/command": "描述"} 格式的命令字典
        """
        logger.info(f"QQ Bot 需要配置以下菜单命令（请在 QQ 开放平台后台手动配置）:")
        for cmd, desc in commands.items():
            logger.info(f"  {cmd} - {desc}")

    async def stop(self):
        """停止渠道"""
        self._should_stop = True

        # 关闭 Bot 客户端
        if self._bot_client:
            try:
                await self._bot_client.close()
            except Exception as e:
                logger.debug(f"QQ Bot 客户端关闭异常: {e}")

        # botpy.close() 只关闭 HTTP，不会结束 WebSocket 协程，需要取消后台任务
        task, self._bot_task = self._bot_task, None
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                logger.debug(f"QQ Bot WebSocket 任务结束异常: {e}")
        self._bot_client = None

        await super().stop()
        logger.info(f"QQ Bot 渠道已停止: {self.name}")

    @staticmethod
    def get_config_schema() -> List[Dict]:
        """返回配置字段定义"""
        return [
            {
                "key": "app_id",
                "label": "App ID",
                "label_en": "App ID",
                "label_tw": "App ID",
                "type": "string",
                "rowGroup": "qq_credential_row",
                "required": True,
                "description": "QQ 开放平台机器人 AppID",
                "description_en": "QQ Bot App ID",
                "description_tw": "QQ 開放平台機器人 AppID",
            },
            {
                "key": "app_secret",
                "label": "App Secret",
                "label_en": "App Secret",
                "label_tw": "App Secret",
                "type": "password",
                "rowGroup": "qq_credential_row",
                "required": True,
                "description": "QQ 开放平台机器人 AppSecret",
                "description_en": "QQ Bot App Secret",
                "description_tw": "QQ 開放平台機器人 AppSecret",
            },
            {
                "key": "user_openid",
                "label": "用户 OpenID",
                "label_en": "User OpenID",
                "label_tw": "用戶 OpenID",
                "type": "string",
                "rowGroup": "qq_openid_row",
                "required": False,
                "description": "默认接收者 openid（单聊），用户需曾与机器人交互过。与「群组 OpenID」二选一",
                "description_en": "Default receiver openid (C2C). User must have interacted with the bot. Choose one of User OpenID or Group OpenID",
                "description_tw": "預設接收者 openid（單聊），用戶需曾與機器人互動過。與「群組 OpenID」二選一",
            },
            {
                "key": "group_openid",
                "label": "群组 OpenID",
                "label_en": "Group OpenID",
                "label_tw": "群組 OpenID",
                "type": "string",
                "rowGroup": "qq_openid_row",
                "required": False,
                "description": "默认群组 openid（群聊）。与「用户 OpenID」二选一",
                "description_en": "Default group openid (Group chat). Choose one of User OpenID or Group OpenID",
                "description_tw": "預設群組 openid（群聊）。與「用戶 OpenID」二選一",
            },
            {
                "key": "admin_whitelist",
                "label": "管理员白名单",
                "label_en": "Admin Whitelist",
                "label_tw": "管理員白名單",
                "type": "string",
                "required": False,
                "description": "可使用管理菜单及命令的用户ID列表，多个ID使用逗号分隔",
                "description_en": "User IDs allowed to use admin menu and commands, separated by commas",
                "description_tw": "可使用管理選單及命令的使用者ID列表，多個ID使用逗號分隔",
            },
            {
                "key": "use_proxy",
                "label": "使用代理",
                "label_en": "Use Proxy",
                "label_tw": "使用代理",
                "type": "boolean",
                "rowGroup": "qq_proxy_row",
                "description": "启用后，Bot 将使用全局代理配置发送请求",
                "description_en": "When enabled, Bot will use global proxy settings for requests",
                "description_tw": "啟用後，Bot 將使用全域代理配置發送請求",
                "default": False,
            },
            {
                "key": "log_raw",
                "label": "记录原始交互",
                "label_en": "Log Raw Interactions",
                "label_tw": "記錄原始互動",
                "type": "boolean",
                "rowGroup": "qq_proxy_row",
                "description": "启用后，Bot 的所有收发消息将记录到 config/logs/bot_raw.log 文件中，用于调试",
                "description_en": "When enabled, all Bot messages will be logged to config/logs/bot_raw.log for debugging",
                "description_tw": "啟用後，Bot 的所有收發訊息將記錄到 config/logs/bot_raw.log 檔案中，用於除錯",
                "default": False,
            },
            IMAGE_MODE_FIELD,
        ]

    # 单张图片下载上限（超过则只给文字描述，不喂给 vision 模型）
    _VISION_MAX_BYTES = 4 * 1024 * 1024

    async def _download_photo_data_url(self, file_url: str) -> Optional[str]:
        """
        把 QQ 图片下载为 data URL（base64），供 vision 模型识别。

        :param file_url: 附件自带的下载地址（botpy 没有 get_file_info 接口，附件对象直接提供 url）
        :return: data URL 或 None（失败时）
        """
        try:
            if not file_url:
                return None
            # 部分附件 url 不带协议头
            if file_url.startswith("//"):
                file_url = "https:" + file_url
            elif not file_url.startswith(("http://", "https://")):
                file_url = "https://" + file_url

            # 下载图片
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning(f"[QQ 图片] 下载失败: status={resp.status}")
                        return None

                    raw = await resp.read()
                    if len(raw) > self._VISION_MAX_BYTES:
                        logger.info(f"[QQ 图片] 图片超过 {self._VISION_MAX_BYTES // 1024 // 1024}MB，跳过识别")
                        return None

                    # 转换为 base64 data URL
                    content_type = resp.headers.get('Content-Type', 'image/jpeg')
                    if 'png' in content_type.lower():
                        mime = 'image/png'
                    elif 'gif' in content_type.lower():
                        mime = 'image/gif'
                    elif 'webp' in content_type.lower():
                        mime = 'image/webp'
                    else:
                        mime = 'image/jpeg'

                    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

        except Exception as e:
            logger.warning(f"[QQ 图片] 下载失败: {e}")
            return None

    async def _normalize_incoming(self, message) -> tuple:
        """
        把一条 QQ 消息规范化为 (给 LLM 的文本, 图片 data URL 列表)。

        处理的类型：
        - 纯文本消息
        - 图片消息（下载并转换为 data URL）
        - 引用消息（提取被引用的内容作为上下文）

        :return: (文本内容, 图片列表)
        """
        parts: List[str] = []
        images: List[str] = []

        # 获取消息内容
        content = getattr(message, "content", "") or ""
        if content:
            parts.append(content.strip())

        # 处理图片附件
        attachments = getattr(message, "attachments", None)
        if attachments and len(attachments) > 0:
            parts.append("[用户发来图片]")
            for attach in attachments:
                # botpy 的附件是 _Attachments 对象，只能按属性访问（没有 .get()）
                if (getattr(attach, "content_type", None) or "").startswith("image/"):
                    file_url = getattr(attach, "url", None)
                    if file_url:
                        data_url = await self._download_photo_data_url(file_url)
                        if data_url:
                            images.append(data_url)
                        else:
                            parts.append("（图片过大或下载失败，无法识别内容，请让用户改用文字描述）")

        # 处理引用消息
        reference = getattr(message, "message_reference", None)
        if reference:
            # reference 是 _MessageRef 对象，不是字典，需要用属性访问
            ref_msg_id = getattr(reference, "message_id", None)
            if ref_msg_id:
                parts.insert(0, f"[用户引用了之前的消息 ID: {ref_msg_id}]")

        # 兜底
        if not parts:
            parts.append("[用户发来一条无法识别的消息]（请让用户改用文字说明需求）")

        return "\n".join(parts), images

    async def _llm_chat_qq(
        self,
        text: str,
        user_id: str,
        user_openid: str,
        reply_msg_id: str,
        message = None,
    ):
        """QQ Bot LLM 对话（非流式，直接发送完整回复）

        QQ Bot API 不支持编辑消息，所以无法像 Telegram 一样实现伪流式。
        只能等 Agent 完成后一次性发送完整回复。

        :param message: 原始消息对象，用于提取图片等附件
        """
        try:
            logger.info(f"[QQ LLM] 开始处理用户消息: user={user_id}, text={text[:50]}...")

            # 规范化消息（提取文本和图片）
            if message:
                llm_text, images = await self._normalize_incoming(message)
            else:
                llm_text, images = text, []

            # 调用 Agent 处理（阻塞等待完整响应）
            result = await self.service.handle_llm_chat(
                text=llm_text or text,
                user_id=user_id,
                images=images if images else None,
                stream_callback=None,  # QQ 不支持流式
                rich_text=False,  # QQ 不支持富文本
                rich_message=False,
                channel=self,
            )

            # 提取响应文本
            if result and hasattr(result, 'text') and result.text:
                response = result.text
            elif isinstance(result, str):
                response = result
            else:
                response = None

            if not response or not response.strip():
                logger.warning(f"[QQ LLM] Agent 返回空响应")
                await self._send_c2c_message(
                    user_openid=user_openid,
                    content="抱歉，我现在无法回复您的消息。",
                    msg_id=reply_msg_id,
                )
                return

            # 发送完整回复
            logger.info(f"[QQ LLM] Agent 响应完成，长度: {len(response)}")
            await self._send_c2c_message(
                user_openid=user_openid,
                content=response,
                keyboard=result.reply_markup if hasattr(result, "reply_markup") else None,
                msg_id=reply_msg_id,
            )

        except Exception as e:
            logger.error(f"[QQ LLM] 处理失败: {e}", exc_info=True)
            try:
                await self._send_c2c_message(
                    user_openid=user_openid,
                    content="抱歉，处理您的消息时出现错误。",
                    msg_id=reply_msg_id,
                )
            except Exception:
                pass

    async def _llm_chat_qq_group(
        self,
        text: str,
        user_id: str,
        group_openid: str,
        reply_msg_id: str,
        message = None,
    ):
        """QQ Bot 群聊 LLM 对话（非流式，直接发送完整回复）

        :param message: 原始消息对象，用于提取图片等附件
        """
        try:
            logger.info(f"[QQ LLM Group] 开始处理群聊消息: group={group_openid}, user={user_id}, text={text[:50]}...")

            # 规范化消息（提取文本和图片）
            if message:
                llm_text, images = await self._normalize_incoming(message)
            else:
                llm_text, images = text, []

            # 调用 Agent 处理（阻塞等待完整响应）
            result = await self.service.handle_llm_chat(
                text=llm_text or text,
                user_id=user_id,
                images=images if images else None,
                stream_callback=None,  # QQ 不支持流式
                rich_text=False,  # QQ 不支持富文本
                rich_message=False,
                channel=self,
            )

            # 提取响应文本
            if result and hasattr(result, 'text') and result.text:
                response = result.text
            elif isinstance(result, str):
                response = result
            else:
                response = None

            if not response or not response.strip():
                logger.warning(f"[QQ LLM Group] Agent 返回空响应")
                await self._send_group_message(
                    group_openid=group_openid,
                    content="抱歉，我现在无法回复您的消息。",
                    msg_id=reply_msg_id,
                )
                return

            # 发送完整回复
            logger.info(f"[QQ LLM Group] Agent 响应完成，长度: {len(response)}")
            await self._send_group_message(
                group_openid=group_openid,
                content=response,
                keyboard=result.reply_markup if hasattr(result, "reply_markup") else None,
                msg_id=reply_msg_id,
            )

        except Exception as e:
            logger.error(f"[QQ LLM Group] 处理失败: {e}", exc_info=True)
            try:
                await self._send_group_message(
                    group_openid=group_openid,
                    content="抱歉，处理您的消息时出现错误。",
                    msg_id=reply_msg_id,
                )
            except Exception:
                pass

