"""
Telegram 通知渠道实现
使用 pyTelegramBotAPI (telebot) 库，支持 Polling 和 Webhook 两种模式。
支持 InlineKeyboard、CallbackQuery、多步对话等交互能力。
"""

import asyncio
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from src._version import APP_VERSION

from src.notification.base import (
    BaseNotificationChannel, CommandResult,
    ChannelCapability, ChannelCapabilities, IMAGE_MODE_FIELD,
)
from src.notification.markdown_converter import (
    markdown_to_v2, strip_markdown, escape_v2,
)

logger = logging.getLogger(__name__)
bot_raw_logger = logging.getLogger("bot_raw")


def _get_telebot():
    """延迟导入 telebot，避免未安装时影响启动"""
    try:
        import telebot
        return telebot
    except ImportError:
        raise ImportError("请安装 pyTelegramBotAPI: pip install pyTelegramBotAPI")


class TelegramChannel(BaseNotificationChannel):
    """Telegram 通知渠道"""

    channel_type = "telegram"
    display_name = "Telegram"

    # Telegram 渠道能力配置
    _CAPABILITIES = ChannelCapabilities(
        capabilities={
            ChannelCapability.INLINE_BUTTONS,
            ChannelCapability.MENU_COMMANDS,
            ChannelCapability.MESSAGE_EDITING,
            ChannelCapability.MESSAGE_DELETION,
            ChannelCapability.CALLBACK_QUERIES,
            ChannelCapability.RICH_TEXT,
            ChannelCapability.RICH_MESSAGE,
            ChannelCapability.IMAGES,
            ChannelCapability.LINKS,
        },
        max_buttons_per_row=4,
        max_button_rows=10,
        max_button_text_length=30,
    )

    def __init__(self, channel_id: int, name: str, config: dict, notification_service):
        super().__init__(channel_id, name, config, notification_service)
        self._bot = None
        self._polling_thread: Optional[threading.Thread] = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # 主事件循环引用
        self._bot_username: str = ""
        self._bot_id: Optional[int] = None
        self._rich_message_available: Optional[bool] = None

    # ── Markdown 格式转换（薄封装，实际逻辑在 markdown_converter 模块）──

    @staticmethod
    def _escape_markdown_v2(text: str) -> str:
        """转义 MarkdownV2 保留字符。"""
        return escape_v2(text)

    @classmethod
    def _markdown_to_v2(cls, text: str) -> str:
        """标准 Markdown → Telegram MarkdownV2。"""
        return markdown_to_v2(text)

    @staticmethod
    def _strip_markdown_v2(text: str) -> str:
        """清洗 Markdown 符号，返回纯文本。"""
        return strip_markdown(text)

    @staticmethod
    def get_config_schema() -> list:
        return [
            {
                "key": "bot_token",
                "label": "Bot Token",
                "type": "password",
                "description": "从 @BotFather 获取的 Bot Token",
                "description_en": "Bot Token obtained from @BotFather",
                "description_tw": "從 @BotFather 取得的 Bot Token",
                "placeholder": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
                "required": True,
            },
            {
                "key": "chat_id",
                "label": "Chat ID",
                "type": "string",
                "rowGroup": "tg_id_row1",
                "description": "默认消息接收者的 Chat ID，用于接收系统通知",
                "description_en": "Default Chat ID for receiving system notifications",
                "description_tw": "預設訊息接收者的 Chat ID，用於接收系統通知",
                "placeholder": "123456789",
            },
            {
                "key": "admin_ids",
                "label": "管理员用户ID",
                "label_en": "Admin User IDs",
                "label_tw": "管理員使用者ID",
                "type": "string",
                "rowGroup": "tg_id_row1",
                "description": "拥有管理权限的用户ID，多个用逗号分隔",
                "description_en": "User IDs with admin privileges, separated by commas",
                "description_tw": "擁有管理權限的使用者ID，多個用逗號分隔",
                "placeholder": "123456789,987654321",
            },
            {
                "key": "allowed_ids",
                "label": "允许的用户ID",
                "label_en": "Allowed User IDs",
                "label_tw": "允許的使用者ID",
                "type": "string",
                "rowGroup": "tg_id_row2",
                "description": "允许使用 Bot 交互的用户ID，多个用逗号分隔。留空则仅管理员可用",
                "description_en": "User IDs allowed to interact with the Bot, separated by commas. Leave empty for admin-only",
                "description_tw": "允許使用 Bot 互動的使用者ID，多個用逗號分隔。留空則僅管理員可用",
                "placeholder": "",
            },
            {
                "key": "mode",
                "label": "交互模式",
                "label_en": "Interaction Mode",
                "label_tw": "互動模式",
                "type": "switch",
                "description": "消息接收方式",
                "description_en": "Message receiving method",
                "description_tw": "訊息接收方式",
                "switchLabels": {"checked": "Webhook", "unchecked": "轮询", "unchecked_en": "Polling", "unchecked_tw": "輪詢"},
                "switchValues": {"checked": "webhook", "unchecked": "polling"},
                "default": "polling",
            },
            {
                "key": "webhook_base_url",
                "label": "外部访问地址",
                "label_en": "External Access URL",
                "label_tw": "外部存取位址",
                "type": "string",
                "description": "你的服务器公网地址（如 https://my-domain.com），系统会自动拼接完整回调路径",
                "description_en": "Your server's public URL (e.g. https://my-domain.com). The system will auto-append the callback path.",
                "description_tw": "你的伺服器公網位址（如 https://my-domain.com），系統會自動拼接完整回呼路徑",
                "placeholder": "https://your-domain.com",
                "visibleWhen": {"mode": "webhook"},
            },
            {
                "key": "tunnel_enabled",
                "label": "启用 VPS 隧道连接",
                "label_en": "Enable VPS Tunnel",
                "label_tw": "啟用 VPS 隧道連接",
                "type": "boolean",
                "description": "启用后，弹幕库将通过上方「外部访问地址」建立 WebSocket 反向隧道，将 Telegram 回调转发到本地（无需公网 IP）",
                "description_en": "When enabled, a WebSocket reverse tunnel is established via the external URL to forward Telegram callbacks locally (no public IP needed).",
                "description_tw": "啟用後，彈幕庫將透過上方「外部存取位址」建立 WebSocket 反向隧道，將 Telegram 回呼轉發到本地（無需公網 IP）",
                "default": False,
                "visibleWhen": {"mode": "webhook"},
            },
            {
                "key": "telegram_api_proxy",
                "label": "API 出网代理地址",
                "label_en": "API Outbound Proxy",
                "label_tw": "API 出網代理位址",
                "type": "string",
                "rowGroup": "tg_id_row2",
                "description": "填入 VPS 地址（如 http://vps.example.com），Bot 的 API 请求将通过 VPS 出网，解决国内 IP 被封锁的问题。留空则直连 api.telegram.org",
                "description_en": "Enter VPS address (e.g. http://vps.example.com). Bot API requests will go through VPS to bypass IP blocks. Leave empty to connect directly to api.telegram.org.",
                "description_tw": "填入 VPS 位址（如 http://vps.example.com），Bot 的 API 請求將透過 VPS 出網，解決國內 IP 被封鎖的問題。留空則直連 api.telegram.org",
                "placeholder": "http://your-vps.com",
            },
            {
                "key": "log_raw",
                "label": "记录原始交互",
                "label_en": "Log Raw Interactions",
                "label_tw": "記錄原始互動",
                "type": "boolean",
                "description": "启用后，Bot 的所有收发消息将记录到 config/logs/bot_raw.log 文件中，用于调试",
                "description_en": "When enabled, all Bot messages will be logged to config/logs/bot_raw.log for debugging.",
                "description_tw": "啟用後，Bot 的所有收發訊息將記錄到 config/logs/bot_raw.log 檔案中，用於除錯",
                "default": False,
            },
            IMAGE_MODE_FIELD,
        ]

    def _is_log_raw(self) -> bool:
        """检查是否启用原始日志"""
        return str(self.config.get("log_raw", "false")).lower() == "true"

    def _log_raw(self, direction: str, data):
        """记录原始交互日志"""
        if self._is_log_raw():
            bot_raw_logger.info(
                f"[TG Bot #{self.channel_id}] {direction}\n"
                f"{json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else data}\n"
                f"{'─' * 60}"
            )

    def _parse_id_list(self, key: str) -> set:
        raw = self.config.get(key, "")
        if not raw:
            return set()
        return {s.strip() for s in str(raw).split(",") if s.strip()}

    def _is_allowed(self, user_id: int) -> bool:
        uid = str(user_id)
        admins = self._parse_id_list("admin_ids")
        allowed = self._parse_id_list("allowed_ids")
        if admins and uid in admins:
            return True
        if allowed:
            return uid in allowed
        # 如果没有配置 allowed_ids，则仅管理员可用
        return uid in admins if admins else True

    async def start(self):
        bot_token = self.config.get("bot_token", "")
        if not bot_token:
            self.logger.warning("Bot Token 未配置，跳过启动")
            return

        # 捕获主事件循环引用，供轮询线程中的回调使用
        self._loop = asyncio.get_running_loop()

        telebot = _get_telebot()

        # 配置出网代理：优先用 telegram_api_proxy（通过 VPS /out/ 路由），否则用全局 proxy_url
        api_proxy = self.config.get("telegram_api_proxy", "").strip().rstrip("/")
        if api_proxy:
            # pyTelegramBotAPI API_URL 格式：{base}/bot{0}/{1}
            telebot.apihelper.API_URL = f"{api_proxy}/out/api.telegram.org/bot{{0}}/{{1}}"
            telebot.apihelper.proxy = None
            self.logger.info(f"Telegram Bot 已启用 VPS 出网代理: {api_proxy}/out/api.telegram.org")
        elif self.proxy_url:
            telebot.apihelper.proxy = {"https": self.proxy_url}
            telebot.apihelper.API_URL = "https://api.telegram.org/bot{0}/{1}"
            self.logger.info(f"Telegram Bot 已启用代理: {self.proxy_url}")
        else:
            # 确保清除可能被其他实例设置过的代理/API URL
            telebot.apihelper.proxy = None
            telebot.apihelper.API_URL = "https://api.telegram.org/bot{0}/{1}"

        # 设置 HTTP 超时，防止代理不可达时 send_message 无限阻塞
        telebot.apihelper.CONNECT_TIMEOUT = 10
        telebot.apihelper.READ_TIMEOUT = 15

        self._bot = telebot.TeleBot(bot_token, threaded=False)

        # 缓存 bot 自身信息（id / username）。
        # why：剥离群聊里的 "@BotName" 提及、以及判断"用户引用的是不是机器人自己的回复"，
        # 都要用到 bot username/id。telebot 的 bot.user 属性需先调用过 get_me 才有值，
        # 这里主动取一次并存下来，失败则降级（提及不剥离，功能不中断）。
        self._bot_username = ""
        self._bot_id = None
        try:
            me = await asyncio.to_thread(self._bot.get_me)
            self._bot_username = getattr(me, "username", "") or ""
            self._bot_id = getattr(me, "id", None)
            self.logger.info(f"Telegram Bot 身份: @{self._bot_username} (id={self._bot_id})")
        except Exception as e:
            self.logger.warning(f"获取 Bot 身份失败（@提及剥离将不生效）: {e}")

        self._register_handlers()

        mode = self.config.get("mode", "polling")
        if mode == "webhook":
            base_url = self.config.get("webhook_base_url", "").rstrip("/")
            if base_url:
                api_key = self.config.get("__webhook_api_key", "")
                full_url = f"{base_url}/api/ui/notification/channels/{self.channel_id}/webhook"
                if api_key:
                    full_url += f"?api_key={api_key}"
                try:
                    self._bot.remove_webhook()
                    self._bot.set_webhook(url=full_url)
                    self.logger.info(f"Telegram Webhook 已设置: {full_url}")
                except Exception as e:
                    self.logger.error(f"设置 Webhook 失败: {e}")
            else:
                self.logger.warning("外部访问地址未配置，无法注册 Webhook")
        else:
            self._start_polling()

        self._running = True

        # 注册菜单命令（BotCommand）
        menu_commands = self.service.get_menu_commands()
        if menu_commands:
            self.register_commands(menu_commands)

    def register_commands(self, commands: Dict[str, str]) -> None:
        """注册 Telegram Bot 菜单命令（BotCommand）
        :param commands: {"/command": "描述"} 格式的命令字典
        """
        if not self._bot:
            return
        try:
            telebot = _get_telebot()
            bot_commands = [
                telebot.types.BotCommand(cmd.lstrip('/'), desc)
                for cmd, desc in commands.items()
            ]
            self._bot.delete_my_commands()
            self._bot.set_my_commands(bot_commands)
            self.logger.info(f"已注册 {len(bot_commands)} 个菜单命令")
        except Exception as e:
            self.logger.error(f"注册菜单命令失败: {e}")

    def _register_handlers(self):
        """注册消息处理器（命令 + 回调查询 + 对话文本）"""
        bot = self._bot

        # ── 命令处理 ──
        @bot.message_handler(commands=[
            'start', 'help', 'status', 'sh', 'search', 'tasks', 'tokens',
            'auto', 'refresh', 'url', 'cache', 'cancel'
        ])
        def handle_command(message):
            self._log_raw("⬇ 收到命令", {"from": message.from_user.id, "text": message.text, "chat_id": message.chat.id})
            if not self._is_allowed(message.from_user.id):
                bot.reply_to(message, "⛔ 你没有权限使用此机器人。")
                return
            cmd = message.text.split()[0].lstrip('/').split('@')[0]
            args = message.text[len(message.text.split()[0]):].strip()
            loop = self._get_event_loop()
            if loop is None:
                bot.reply_to(message, "⚠️ 服务正在启动或关闭中，请稍后再试。")
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_async_command(cmd, message, args), loop
            )

        # ── InlineKeyboard 回调查询处理 ──
        @bot.callback_query_handler(func=lambda call: True)
        def handle_callback_query(call):
            self._log_raw("⬇ 收到回调", {"from": call.from_user.id, "data": call.data, "chat_id": call.message.chat.id if call.message else None})
            if not self._is_allowed(call.from_user.id):
                bot.answer_callback_query(call.id, "⛔ 无权限")
                return
            loop = self._get_event_loop()
            if loop is None:
                bot.answer_callback_query(call.id, "⚠️ 服务不可用")
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_async_callback(call), loop
            )

        # ── 普通文本消息处理（用于对话状态机） ──
        @bot.message_handler(func=lambda m: True, content_types=['text'])
        def handle_text_message(message):
            self._log_raw("⬇ 收到文本", {"from": message.from_user.id, "text": message.text, "chat_id": message.chat.id})
            if not self._is_allowed(message.from_user.id):
                return
            loop = self._get_event_loop()
            if loop is None:
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_async_text(message), loop
            )

        # ── 非文本消息处理（贴纸/图片/语音/文件/位置等）──
        # why：原先只注册 ['text']，其余类型被 telebot 直接丢弃，用户发贴纸或图片时
        # 机器人毫无反应。这里统一收下，交给 _normalize_incoming 翻译成 LLM 可理解的描述。
        @bot.message_handler(
            func=lambda m: True,
            content_types=[
                'sticker', 'photo', 'voice', 'audio', 'video', 'video_note',
                'animation', 'document', 'location', 'venue', 'contact',
                'poll', 'dice',
            ],
        )
        def handle_rich_message(message):
            self._log_raw("⬇ 收到富消息", {
                "from": message.from_user.id,
                "type": message.content_type,
                "chat_id": message.chat.id,
            })
            if not self._is_allowed(message.from_user.id):
                return
            loop = self._get_event_loop()
            if loop is None:
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_async_rich_message(message), loop
            )


    def _get_event_loop(self):
        """获取主事件循环（使用 start() 时捕获的引用）"""
        if self._loop and self._loop.is_running():
            return self._loop
        self.logger.warning("主事件循环不可用，命令将无法执行")
        return None

    async def _handle_async_command(self, cmd: str, message, args: str):
        """异步处理命令 — 调用服务层并渲染结果"""
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        # cancel 命令直接清除对话状态
        if cmd == "cancel":
            result = await self.service.handle_cancel(user_id)
        else:
            result: CommandResult = await self.service.handle_command(
                cmd, user_id, args, self, chat_id=chat_id
            )
        await self._render_result(result, chat_id, reply_to_message_id=message.message_id)

    async def _handle_async_callback(self, call):
        """异步处理 InlineKeyboard 回调"""
        user_id = str(call.from_user.id)
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        callback_data = call.data or ""
        result: CommandResult = await self.service.handle_callback(
            callback_data, user_id, self, chat_id=chat_id, message_id=message_id
        )
        # 应答回调（消除 TG 客户端的加载动画）
        try:
            await asyncio.to_thread(
                self._bot.answer_callback_query,
                call.id, text=result.answer_callback_text or ""
            )
        except Exception:
            pass
        await self._render_result(result, chat_id)

    async def _handle_async_text(self, message):
        """异步处理普通文本消息（对话状态机中的用户输入）"""
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        text = (message.text or "").strip()
        try:
            # 若无活跃命令流程且御坂 LLM 可用 → 走伪流式对话（Telegram 支持 edit）
            conv = self.service.get_conversation(user_id)
            if not conv and await self.service.is_llm_chat_enabled():
                # 纯文本也可能带引用/@提及/转发标记，统一规范化后再交给 LLM，
                # 否则「引用某条消息 + 追问」时模型不知道用户在指哪条
                llm_text, images = await self._normalize_incoming(message)
                await self._llm_chat_stream(
                    llm_text or text, user_id, chat_id, message.message_id, images=images
                )
                return

            result: CommandResult = await self.service.handle_text_input(
                text, user_id, self, chat_id=chat_id
            )
            if result is None:
                return
            if result and result.text:
                await self._render_result(result, chat_id, reply_to_message_id=message.message_id)
        except Exception as e:
            self.logger.error(f"[文本处理] 处理失败 user={user_id}: {e}", exc_info=True)

    async def _handle_async_rich_message(self, message):
        """
        异步处理非纯文本消息（贴纸/图片/语音/文件/位置/联系人等）。

        统一交给 _normalize_incoming 翻译成「文本描述 + 可选图片」，再走 LLM 对话。
        why：原先 handler 只注册 content_types=['text']，其余类型直接被 telebot 丢弃，
        用户发贴纸或图片时机器人完全无反应，看起来像卡死。
        """
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        try:
            # 非文本消息不参与命令对话状态机（状态机只认文本输入），
            # 有活跃流程时提示用户先完成或取消，避免静默吞掉消息
            conv = self.service.get_conversation(user_id)
            if conv:
                await asyncio.to_thread(
                    self._bot.send_message, chat_id,
                    "当前有正在进行的操作，请先完成或发送 /cancel 取消，之后再发送这类消息。",
                    reply_to_message_id=message.message_id,
                )
                return

            if not await self.service.is_llm_chat_enabled():
                return

            text, images = await self._normalize_incoming(message)
            if not text and not images:
                return
            await self._llm_chat_stream(
                text, user_id, chat_id, message.message_id, images=images
            )
        except Exception as e:
            self.logger.error(f"[富消息处理] 处理失败 user={user_id}: {e}", exc_info=True)

    # 单张图片下载上限（超过则只给文字描述，不喂给 vision 模型）
    _VISION_MAX_BYTES = 4 * 1024 * 1024

    async def _download_photo_data_url(self, file_id: str) -> Optional[str]:
        """
        把 Telegram 图片下载为 data URL（base64），供 vision 模型识别。

        why：Telegram 的文件直链带 bot token 且有时效，不能直接给第三方 LLM；
        统一转 data URL 内联，既避免泄露 token，也不依赖外网可达性。
        失败返回 None，由调用方降级为纯文字描述。
        """
        try:
            info = await asyncio.to_thread(self._bot.get_file, file_id)
            size = getattr(info, "file_size", 0) or 0
            if size and size > self._VISION_MAX_BYTES:
                self.logger.info(f"[富消息] 图片超过 {self._VISION_MAX_BYTES // 1024 // 1024}MB，跳过识别")
                return None
            raw = await asyncio.to_thread(self._bot.download_file, info.file_path)
            if not raw or len(raw) > self._VISION_MAX_BYTES:
                return None
            import base64
            path = (info.file_path or "").lower()
            if path.endswith(".png"):
                mime = "image/png"
            elif path.endswith(".webp"):
                mime = "image/webp"
            elif path.endswith(".gif"):
                mime = "image/gif"
            else:
                mime = "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        except Exception as e:
            self.logger.warning(f"[富消息] 下载图片失败: {e}")
            return None

    @staticmethod
    def _describe_user(user) -> str:
        """把 Telegram User 对象描述成「昵称(@username)」，供引用/转发场景标注来源。"""
        if not user:
            return "某人"
        name = " ".join(
            p for p in (getattr(user, "first_name", ""), getattr(user, "last_name", "")) if p
        ).strip()
        uname = getattr(user, "username", "")
        if name and uname:
            return f"{name}(@{uname})"
        return name or (f"@{uname}" if uname else "某人")

    @classmethod
    def _summarize_message(cls, msg) -> str:
        """
        把任意 Telegram 消息概括成一行文本，供「引用消息」标注被引内容。

        只概括被引消息本身，不再递归展开它的引用/转发链：对 LLM 理解无益，
        还会撑大 token。
        """
        if msg is None:
            return ""
        # 有文字/图片说明就直接用，这是最常见情况
        body = (getattr(msg, "text", "") or getattr(msg, "caption", "") or "").strip()
        if body:
            return body if len(body) <= 300 else body[:300] + "…"
        # 无文字则按媒体类型给个占位描述
        if getattr(msg, "sticker", None):
            emoji = getattr(msg.sticker, "emoji", "") or ""
            return f"[贴纸 {emoji}]".replace(" ]", "]")
        if getattr(msg, "photo", None):
            return "[图片]"
        if getattr(msg, "voice", None):
            return "[语音]"
        if getattr(msg, "video", None):
            return "[视频]"
        if getattr(msg, "animation", None):
            return "[GIF 动图]"
        if getattr(msg, "audio", None):
            return "[音频]"
        if getattr(msg, "document", None):
            return f"[文件 {getattr(msg.document, 'file_name', '') or ''}]".replace(" ]", "]")
        if getattr(msg, "location", None):
            return "[位置]"
        if getattr(msg, "contact", None):
            return "[联系人名片]"
        if getattr(msg, "poll", None):
            return f"[投票 {getattr(msg.poll, 'question', '') or ''}]".replace(" ]", "]")
        return "[非文本消息]"

    async def _normalize_incoming(self, message) -> tuple:
        """
        把一条 Telegram 消息规范化为 (给 LLM 的文本, 图片 data URL 列表)。

        设计原则：LLM 只认文本和图片，所以其余类型统一翻译成「带方括号标注的
        自然语言描述」，让模型知道用户发了什么、能接着聊，而不是收到空字符串。

        处理的类型：
        - 引用消息(reply_to_message)：把被引内容作为上下文前置，让 LLM 知道在说哪条
        - @提及：剥掉 @机器人用户名，只留真正的诉求（群里 @Bot 提问的标准姿势）
        - 贴纸：给出 emoji 与贴纸集名；静态贴纸额外下载图像交 vision 识别
        - 图片：下载交 vision 识别，caption 作为提问文本
        - 语音/音频/视频/动图/文件/位置/联系人/投票：给出文字描述与元信息
        - 转发消息：标注原作者
        """
        parts: List[str] = []
        images: List[str] = []

        # ── 引用消息：把被引内容作为上下文前置 ──
        replied = getattr(message, "reply_to_message", None)
        if replied:
            who = self._describe_user(getattr(replied, "from_user", None))
            # 被引的是机器人自己 → 说明用户在追问上一条回复
            me_id = getattr(self, "_bot_id", None)
            replied_uid = getattr(getattr(replied, "from_user", None), "id", None)
            if me_id and replied_uid == me_id:
                parts.append(f"[用户引用了你之前的回复]「{self._summarize_message(replied)}」")
            else:
                parts.append(f"[用户引用了 {who} 的消息]「{self._summarize_message(replied)}」")

        # ── 转发消息：标注原始来源 ──
        fwd_from = getattr(message, "forward_from", None)
        fwd_chat = getattr(message, "forward_from_chat", None)
        if fwd_from or fwd_chat:
            src = (
                self._describe_user(fwd_from) if fwd_from
                else (getattr(fwd_chat, "title", "") or "某频道")
            )
            parts.append(f"[这是从 {src} 转发的消息]")

        text = (getattr(message, "text", "") or "").strip()
        caption = (getattr(message, "caption", "") or "").strip()

        # ── 剥离 @机器人提及：群聊里 "@MyBot 帮我查X" 应只把 "帮我查X" 交给 LLM ──
        bot_username = getattr(self, "_bot_username", "")
        if bot_username:
            mention = f"@{bot_username}"
            text = text.replace(mention, "").strip()
            caption = caption.replace(mention, "").strip()

        # ── 各媒体类型：翻译成描述，必要时下载图像 ──
        sticker = getattr(message, "sticker", None)
        if sticker:
            emoji = getattr(sticker, "emoji", "") or ""
            set_name = getattr(sticker, "set_name", "") or ""
            desc = "[用户发来一张贴纸"
            if emoji:
                desc += f"，对应表情 {emoji}"
            if set_name:
                desc += f"，来自贴纸包「{set_name}」"
            desc += "]"
            parts.append(desc)
            # 动态贴纸(webm/tgs)无法作为静态图识别，只有静态 webp 才喂 vision
            if not getattr(sticker, "is_animated", False) and not getattr(sticker, "is_video", False):
                data_url = await self._download_photo_data_url(sticker.file_id)
                if data_url:
                    images.append(data_url)
            # 纯贴纸无文字时，给 LLM 一个明确的行为指引，避免它答"我看不到图"
            if not text and not caption:
                parts.append("请结合这张贴纸的情绪自然回应用户。")

        photos = getattr(message, "photo", None)
        if photos:
            parts.append("[用户发来一张图片]")
            # telebot 的 photo 是不同尺寸列表，取最后一个（分辨率最高）
            data_url = await self._download_photo_data_url(photos[-1].file_id)
            if data_url:
                images.append(data_url)
            else:
                parts.append("（图片过大或下载失败，无法识别内容，请让用户改用文字描述）")

        voice = getattr(message, "voice", None)
        if voice:
            parts.append(
                f"[用户发来一条语音，时长 {getattr(voice, 'duration', 0)} 秒]"
                "（你无法收听语音，请礼貌请用户改用文字）"
            )

        audio = getattr(message, "audio", None)
        if audio:
            title = getattr(audio, "title", "") or getattr(audio, "file_name", "") or "未命名"
            parts.append(f"[用户发来一个音频文件「{title}」]（你无法收听音频内容）")

        video = getattr(message, "video", None)
        if video:
            parts.append(
                f"[用户发来一段视频，时长 {getattr(video, 'duration', 0)} 秒]"
                "（你无法观看视频内容）"
            )

        video_note = getattr(message, "video_note", None)
        if video_note:
            parts.append("[用户发来一条圆形视频消息]（你无法观看视频内容）")

        animation = getattr(message, "animation", None)
        if animation:
            parts.append("[用户发来一个 GIF 动图]（你无法观看动图内容）")

        document = getattr(message, "document", None)
        # 注意：GIF/视频类消息也会带 document 字段，已被上面的 animation/video 分支覆盖，
        # 这里只处理真正的文件，避免同一条消息被描述两次
        if document and not animation and not video:
            fname = getattr(document, "file_name", "") or "未命名文件"
            fsize = getattr(document, "file_size", 0) or 0
            size_txt = f"，约 {fsize // 1024} KB" if fsize else ""
            parts.append(f"[用户发来一个文件「{fname}」{size_txt}]（你无法读取文件内容）")

        location = getattr(message, "location", None)
        if location:
            parts.append(
                f"[用户分享了一个位置，经纬度 "
                f"{getattr(location, 'latitude', '?')},{getattr(location, 'longitude', '?')}]"
            )

        contact = getattr(message, "contact", None)
        if contact:
            cname = " ".join(
                p for p in (
                    getattr(contact, "first_name", ""), getattr(contact, "last_name", "")
                ) if p
            ).strip() or "某人"
            parts.append(f"[用户分享了「{cname}」的联系人名片]")

        poll = getattr(message, "poll", None)
        if poll:
            parts.append(f"[用户发来一个投票：{getattr(poll, 'question', '') or ''}]")

        dice = getattr(message, "dice", None)
        if dice:
            parts.append(
                f"[用户投出了一个骰子，表情 {getattr(dice, 'emoji', '🎲')}，"
                f"点数 {getattr(dice, 'value', '?')}]"
            )

        # ── 用户自己的文字放最后，作为真正的诉求 ──
        body = text or caption
        if body:
            parts.append(body)

        # 什么都没识别出来时给个兜底，至少让 LLM 能回应
        if not parts:
            parts.append("[用户发来一条无法识别的消息]（请让用户改用文字说明需求）")

        return "\n".join(parts), images

    # ── 富消息发送（Bot API 10.1） ──

    # 判定"服务端不支持富消息"的错误特征。
    # why：telebot 抛的是通用 ApiTelegramException，只能靠 description 文本区分
    # "服务端没这个能力"（应永久降级）和"这条内容有问题/网络抖动"（下一帧还能试）。
    # 注意：不能把宽泛的 "not found" 放进来——"message to edit not found"（用户删了
    # 占位消息）与富消息能力无关，误判会让整个进程错误地永久降级。
    _RICH_UNSUPPORTED_SIGNS = (
        "method not found",           # 服务端未部署 sendRichMessage
        "unknown method",
        "method is not available",
        "unsupported parameter",      # 旧版服务端不认 rich_message 字段
        "unknown parameter",
        "unknown field",
        'field "rich_message"',
    )

    def _use_rich_message(self) -> bool:
        """当前是否应该走结构化富消息。

        库层声明能力 ∧ 运行时未被判定不可用。首次调用时 _rich_message_available
        为 None（未探测），按"乐观尝试"返回 True，由实际发送结果来确认。
        """
        if not self.get_capabilities().supports(ChannelCapability.RICH_MESSAGE):
            return False
        return self._rich_message_available is not False

    def _build_rich_message(self, markdown: str, image_url: str = ""):
        """把 Markdown 文本包装成 InputRichMessage。

        why：富消息的 markdown 字段与 GitHub Flavored Markdown 兼容，标准 Markdown
        （**粗体**、# 标题、- 列表、``` 代码块、> 引用、| 表格 |）可以零转换直传。

        :param image_url: 可选的图片 HTTPS URL。非空时以媒体块内嵌到正文顶部。
            注意只支持 HTTP(S) URL——telebot 的 send_rich_message/edit_message_text
            走的是 params（表单编码），没有 multipart 通道，本地字节流无法上传。

        skip_entity_detection 不启用：让 Telegram 自动识别 URL、@提及、/命令等，
        回复里出现的链接和命令能直接点击。
        """
        telebot = _get_telebot()
        if image_url:
            # 媒体块必须独立成段（官方："Media can be specified only as a separate block"）
            markdown = f"![]({image_url})\n\n{markdown}"
        return telebot.types.InputRichMessage(markdown=markdown)

    def _note_rich_failure(self, err: Exception, context: str) -> bool:
        """归类一次富消息发送失败，维护 _rich_message_available 标志。

        :return: True 表示"服务端不支持"（已永久降级），False 表示属于内容/网络层
            问题（能力判定不变，仅本条降级）。
        """
        err_str = str(err).lower()
        if any(sign in err_str for sign in self._RICH_UNSUPPORTED_SIGNS):
            if self._rich_message_available is not False:
                self._rich_message_available = False
                self.logger.warning(
                    "Telegram 服务端不支持富消息（sendRichMessage），"
                    f"已降级为传统发送方式：{err}"
                )
            return True
        self.logger.warning(f"{context}富消息发送失败，本条降级处理: {err}")
        return False

    def _mark_rich_ok(self) -> None:
        """标记富消息通路可用（首次成功时记一条日志，便于排查）。"""
        if self._rich_message_available is None:
            self._rich_message_available = True
            self.logger.info(
                "Telegram 富消息（sendRichMessage）可用，已启用结构化排版"
            )

    async def _rich_edit(self, chat_id, msg_id: int, markdown: str,
                         image_url: str = "", markup=None) -> bool:
        """用富消息编辑已有消息。返回是否成功；失败时已完成能力标志维护。

        markup 必须一并传入：editMessageText 不带 reply_markup 会清空原有按钮。
        """
        if not self._use_rich_message():
            return False
        try:
            await asyncio.to_thread(
                self._bot.edit_message_text,
                chat_id=chat_id, message_id=msg_id,
                rich_message=self._build_rich_message(markdown, image_url),
                reply_markup=markup,
            )
            self._mark_rich_ok()
            return True
        except Exception as err:
            # 内容与当前完全一致：Telegram 报 not modified，实质是成功（通路也正常）
            if "not modified" in str(err).lower():
                self._mark_rich_ok()
                return True
            self._note_rich_failure(err, "编辑消息")
            return False

    async def _rich_send(self, chat_id, markdown: str, image_url: str = "",
                         markup=None, reply_to_message_id: Optional[int] = None):
        """用富消息发送新消息。返回 Message 对象，失败返回 None。"""
        if not self._use_rich_message():
            return None
        try:
            telebot = _get_telebot()
            reply_params = None
            if reply_to_message_id:
                # 富消息没有 reply_to_message_id 参数，只接受 ReplyParameters 对象
                reply_params = telebot.types.ReplyParameters(
                    message_id=reply_to_message_id
                )
            sent = await asyncio.to_thread(
                self._bot.send_rich_message,
                chat_id,
                self._build_rich_message(markdown, image_url),
                reply_markup=markup,
                reply_parameters=reply_params,
            )
            self._mark_rich_ok()
            return sent
        except Exception as err:
            self._note_rich_failure(err, "发送消息")
            return None

    async def _edit_llm_frame(
        self, chat_id, msg_id: int, content: str, is_final: bool = False
    ) -> bool:
        """把一帧 LLM 回复内容写入占位消息。

        两级：富消息（标准 Markdown 直传）→ 纯文本兜底。
        中间不再插 MarkdownV2 一级——LLM 输出的就是标准 Markdown，富消息原生吃下；
        富消息不可用时说明服务端版本过旧，直接给纯文本，不为此维护第三套转义。

        :param is_final: 是否为最终定稿帧。中途帧失败属正常（限流、内容未变），
            静默跳过；最终帧失败要记警告，因为用户会看到不完整的回复。
        :return: 是否成功写入
        """
        if await self._rich_edit(chat_id, msg_id, content):
            return True

        # 纯文本兜底：剥掉 Markdown 符号，避免用户看到裸露的 ** 和 [文字](URL)。
        # 必须先过 _markdown_to_v2 再 strip，不能直接 strip——
        # why：_strip_markdown_v2 只处理转义反斜杠和 */` 符号，不认表格。
        # 直接 strip 会把 | 源 | 集数 | 和 |:---|---:| 原样留下，
        # 正是最初要解决的"裸竖线堆叠"问题。表格降级逻辑在 _markdown_to_v2
        # 的 _convert_table 里，走一遍才能转成「每条一段」列表。
        try:
            await asyncio.to_thread(
                self._bot.edit_message_text,
                self._strip_markdown_v2(self._markdown_to_v2(content)),
                chat_id, msg_id,
            )
            return True
        except Exception as err:
            if is_final:
                self.logger.warning(f"LLM 回复发送失败: {err}")
            return False

    async def _llm_chat_stream(
        self, text: str, user_id: str, chat_id, reply_to_id: int,
        images: Optional[List[str]] = None,
    ):
        """御坂 LLM 伪流式：先发占位消息，随增量限流 edit 更新（Telegram 专属）。

        images：图片 data URL 列表（贴纸/图片消息经 _normalize_incoming 提取），
        透传给 Agent 交 vision 模型识别。
        """
        import time as _t

        # ① 先发 typing action，让用户看到"正在输入…"状态
        typing_task = None
        typing_active = {"stop": False}

        async def maintain_typing():
            """后台任务：每 4.5 秒重发 typing，直到占位消息发出（typing 状态维持 5 秒）"""
            while not typing_active["stop"]:
                try:
                    await asyncio.to_thread(self._bot.send_chat_action, chat_id, "typing")
                except Exception as e:
                    self.logger.debug(f"[Typing] 发送失败: {e}")
                await asyncio.sleep(4.5)

        try:
            # 启动 typing 维持任务
            typing_task = asyncio.create_task(maintain_typing())

            # ② 发占位消息拿 message_id（此时 typing 任务已在后台运行）
            placeholder = await asyncio.to_thread(
                self._bot.send_message, chat_id, "御坂御坂正在思考…", reply_to_message_id=reply_to_id
            )
            msg_id = placeholder.message_id

            # 占位消息已发出，但 LLM 首个 token 可能仍需数秒，
            # 故 typing 状态持续维持，直到首次真正把增量内容 edit 进占位消息为止。

            last_edit = {"t": 0.0, "shown": ""}

            async def on_stream(partial: str):
                # 限流：距上次 edit ≥1.3s 才更新，避免触发 Telegram 速率限制
                now = _t.monotonic()
                if now - last_edit["t"] < 1.3 or partial == last_edit["shown"]:
                    return
                last_edit["t"] = now
                last_edit["shown"] = partial
                # 首次成功刷出增量内容后停止 typing（用户已看到实际内容在生成）
                typing_active["stop"] = True
                # 中途帧允许失败：这一帧刷不出去，下一帧或最终定稿会补上，
                # 故不记录警告日志，避免流式过程中刷屏。
                await self._edit_llm_frame(chat_id, msg_id, partial or "…", is_final=False)

            # rich_message 反映"结构化富消息是否真正可用"：库层能力 ∧ 运行时未被判定不可用。
            # 富消息的 markdown 与 GFM 兼容，表格/标题/公式全部原生支持，
            # 因此这里为 True 时 prompt 会放开表格；若运行时探测到不可用会自动收紧。
            result = await self.service.handle_llm_chat(
                text, user_id, stream_callback=on_stream, images=images,
                rich_text=self.get_capabilities().supports(ChannelCapability.RICH_TEXT),
                rich_message=self._use_rich_message(),
            )
            final = (result.text if result else "") or "……"
            # 最终定稿：这一帧必须成功，失败会逐级降级并记录日志
            if final != last_edit["shown"]:
                await self._edit_llm_frame(chat_id, msg_id, final, is_final=True)
        finally:
            # 确保 typing 任务被清理
            typing_active["stop"] = True
            if typing_task and not typing_task.done():
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass

    # ── 渲染引擎 ──

    def _build_inline_markup(self, buttons: List[List[Dict[str, str]]]):
        """将平台无关的按钮定义转换为 telebot InlineKeyboardMarkup"""
        telebot = _get_telebot()
        markup = telebot.types.InlineKeyboardMarkup()
        for row in buttons:
            btn_row = []
            for btn in row:
                btn_row.append(telebot.types.InlineKeyboardButton(
                    text=btn.get("text", ""),
                    callback_data=btn.get("callback_data", "noop"),
                ))
            markup.row(*btn_row)
        return markup

    async def _edit_with_retry(self, chat_id, message_id, text,
                               markup=None, parse_mode=None,
                               max_retries: int = 3, retry_delay: float = 5.0) -> bool:
        """带重试的消息编辑，网络瞬断时自动重试。返回是否成功。"""
        for attempt in range(max_retries):
            try:
                await asyncio.to_thread(
                    self._bot.edit_message_text,
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=markup,
                    parse_mode=parse_mode,
                )
                return True
            except Exception as edit_err:
                err_str = str(edit_err).lower()
                if "message is not modified" in err_str:
                    return True  # 内容未变化，视为成功
                elif "no text in the message" in err_str:
                    try:
                        await asyncio.to_thread(
                            self._bot.edit_message_caption,
                            caption=text,
                            chat_id=chat_id,
                            message_id=message_id,
                            reply_markup=markup,
                            parse_mode=parse_mode,
                        )
                        return True
                    except Exception as cap_err:
                        if "message is not modified" in str(cap_err).lower():
                            return True
                        # caption 编辑失败也重试
                elif "connection" in err_str or "timeout" in err_str or "reset" in err_str:
                    # 网络瞬断，等待后重试
                    if attempt < max_retries - 1:
                        self.logger.warning(
                            f"编辑消息网络异常 (第{attempt+1}次)，{retry_delay}秒后重试: "
                            f"{type(edit_err).__name__}"
                        )
                        await asyncio.sleep(retry_delay)
                        continue
                elif "can't parse entities" in err_str:
                    # MarkdownV2 解析失败，不重试，返回 False 让调用方降级为纯文本
                    self.logger.warning(f"编辑消息 MarkdownV2 解析失败，将降级为纯文本: {edit_err}")
                    return False
                else:
                    # 其他错误直接抛出
                    raise edit_err
        return False

    async def _render_photo_bytes(self, result, chat_id, markup,
                                  parse_mode, reply_to_message_id):
        """发送聚合海报图（PNG bytes）。

        Telegram 图片消息的图片本身无法 edit，因此翻页场景（edit_message_id 非空）
        采用「先删旧消息，再发新图」策略，保证每页都能换成对应的九宫格海报。
        caption 长度上限 1024，超出时截断。
        """
        import io as _io
        caption = result.text or ""
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        # 翻页：先删除旧消息（图片无法 edit）
        if result.edit_message_id:
            try:
                await asyncio.to_thread(
                    self._bot.delete_message, chat_id, result.edit_message_id
                )
            except Exception as del_err:
                self.logger.debug(f"删除旧海报消息失败（忽略）: {del_err}")

        sent = None
        try:
            photo = _io.BytesIO(result.image_bytes)
            photo.name = "poster.png"
            sent = await asyncio.to_thread(
                self._bot.send_photo, chat_id, photo,
                caption=caption, reply_markup=markup,
                parse_mode=parse_mode, reply_to_message_id=reply_to_message_id,
            )
        except Exception as photo_err:
            err_str = str(photo_err).lower()
            if "can't parse entities" in err_str:
                # caption 解析失败：去掉 parse_mode 重发
                try:
                    photo = _io.BytesIO(result.image_bytes)
                    photo.name = "poster.png"
                    sent = await asyncio.to_thread(
                        self._bot.send_photo, chat_id, photo,
                        caption=caption, reply_markup=markup,
                        reply_to_message_id=reply_to_message_id,
                    )
                except Exception as e2:
                    self.logger.warning(f"send_photo(bytes) 重试失败，降级纯文本: {e2}")
            else:
                self.logger.warning(f"send_photo(bytes) 失败，降级纯文本: {photo_err}")
            if sent is None:
                # 最终降级：发纯文本列表，至少保证用户能选
                try:
                    sent = await asyncio.to_thread(
                        self._bot.send_message, chat_id, result.text,
                        reply_markup=markup, parse_mode=parse_mode,
                        reply_to_message_id=reply_to_message_id,
                    )
                except Exception:
                    try:
                        sent = await asyncio.to_thread(
                            self._bot.send_message, chat_id, result.text,
                            reply_markup=markup,
                        )
                    except Exception:
                        pass

        # 回写新消息 id，供后续翻页 edit/删除使用
        if sent and result.next_state:
            self.service.update_conversation_message_id(str(chat_id), sent.message_id)

    async def _render_result(self, result: CommandResult, chat_id: int,
                             reply_to_message_id: int = None):
        """根据 CommandResult 渲染消息（发送新消息或编辑已有消息）
        所有 TG Bot API 调用通过 asyncio.to_thread 在线程池中执行，避免阻塞事件循环。
        """
        if not result or not result.text:
            return
        try:
            markup = None
            if result.reply_markup:
                markup = self._build_inline_markup(result.reply_markup)

            parse_mode = result.parse_mode

            # 聚合海报图：优先以图片消息（bytes）发送。
            # 翻页等编辑场景下图片本身无法 edit，需删除旧消息后发新图。
            if result.image_bytes:
                await self._render_photo_bytes(
                    result, chat_id, markup, parse_mode, reply_to_message_id
                )
                return

            if result.edit_message_id:
                self._log_raw("⬆ 编辑消息", {"chat_id": chat_id, "message_id": result.edit_message_id, "text": result.text[:200]})
                success = await self._edit_with_retry(
                    chat_id, result.edit_message_id, result.text,
                    markup=markup, parse_mode=parse_mode,
                )
                if not success:
                    # 重试全部失败，降级为发新消息
                    self.logger.warning(f"编辑消息重试全部失败，降级为发新消息")
                    sent = await asyncio.to_thread(
                        self._bot.send_message, chat_id, result.text,
                        reply_markup=markup, parse_mode=parse_mode,
                    )
                    if result.task_id and sent and hasattr(self.service, '_task_progress_tg_msg'):
                        self.service._task_progress_tg_msg.setdefault(
                            result.task_id, {}
                        )[self.channel_id] = sent.message_id
            else:
                cover_url = ""
                # why：交互卡片不走 send_rendered，外链模式下需先本地化海报地址。
                articles = await self.localize_articles(result.articles)
                if articles:
                    for a in articles:
                        if a.get("picurl"):
                            cover_url = a["picurl"]
                            break

                if cover_url:
                    self._log_raw("⬆ 发送图文消息", {"chat_id": chat_id, "photo": cover_url, "text": result.text[:200]})
                    caption_text = result.text[:1024] if len(result.text) > 1024 else result.text
                    try:
                        sent = await asyncio.to_thread(
                            self._bot.send_photo,
                            chat_id,
                            cover_url,
                            caption=caption_text,
                            reply_markup=markup,
                            parse_mode=parse_mode,
                            reply_to_message_id=reply_to_message_id,
                        )
                    except Exception as photo_err:
                        self.logger.warning(f"send_photo 失败，降级为纯文本: {photo_err}")
                        sent = await asyncio.to_thread(
                            self._bot.send_message,
                            chat_id,
                            result.text,
                            reply_markup=markup,
                            parse_mode=parse_mode,
                            reply_to_message_id=reply_to_message_id,
                        )
                else:
                    self._log_raw("⬆ 发送消息", {"chat_id": chat_id, "text": result.text[:200]})
                    sent = await asyncio.to_thread(
                        self._bot.send_message,
                        chat_id,
                        result.text,
                        reply_markup=markup,
                        parse_mode=parse_mode,
                        reply_to_message_id=reply_to_message_id,
                    )
                if result.next_state and sent:
                    self.service.update_conversation_message_id(
                        str(chat_id), sent.message_id
                    )
                if result.task_id and sent and hasattr(self.service, '_task_progress_tg_msg'):
                    self.service._task_progress_tg_msg.setdefault(
                        result.task_id, {}
                    )[self.channel_id] = sent.message_id
        except Exception as e:
            self.logger.error(f"渲染消息失败: {e}")
            try:
                await asyncio.to_thread(self._bot.send_message, chat_id, result.text, reply_markup=markup)
            except Exception:
                try:
                    await asyncio.to_thread(self._bot.send_message, chat_id, result.text)
                except Exception:
                    pass

    def _start_polling(self):
        """在后台线程中启动长轮询"""
        if self._polling_thread and self._polling_thread.is_alive():
            return

        # 压制 telebot / urllib3 的 SSL 瞬断噪音日志（这类错误 infinity_polling 会自动重试）
        import logging as _logging
        _logging.getLogger("urllib3.connectionpool").setLevel(_logging.CRITICAL)
        _logging.getLogger("telebot").setLevel(_logging.WARNING)

        def polling_worker():
            self.logger.info("Telegram 轮询已启动")
            try:
                self._bot.remove_webhook()
            except Exception:
                pass

            # 自行实现轮询循环，替代 infinity_polling 以控制日志输出
            while self._running:
                try:
                    self._bot.polling(non_stop=True, timeout=30, long_polling_timeout=30, logger_level=0)
                except Exception as e:
                    if not self._running:
                        break
                    # 提取简洁的错误摘要：类型 + 核心信息（去掉嵌套的 Caused by 链）
                    err_type = type(e).__name__
                    err_msg = str(e)
                    # 从嵌套异常链中提取最内层的关键信息
                    if "Caused by" in err_msg:
                        # 取最后一个 Caused by 后面的内容
                        caused = err_msg.rsplit("Caused by ", 1)[-1].rstrip(")")
                        short_msg = caused
                    elif len(err_msg) > 200:
                        short_msg = err_msg[:200] + "..."
                    else:
                        short_msg = err_msg
                    self.logger.warning(f"Telegram 轮询网络异常（自动重试）: {err_type}: {short_msg}")
                    time.sleep(3)

        self._polling_thread = threading.Thread(
            target=polling_worker,
            name=f"tg-poll-{self.channel_id}",
            daemon=True,
        )
        self._polling_thread.start()

    async def stop(self):
        self._running = False
        self._loop = None  # 清除事件循环引用，防止关闭后仍有 coroutine 被调度
        if self._bot:
            try:
                self._bot.stop_polling()
            except Exception:
                pass
            try:
                self._bot.remove_webhook()
            except Exception:
                pass
        self._bot = None
        self.logger.info("Telegram 渠道已停止")

    async def send_message(self, title: str, text: str, **kwargs):
        if not self._bot:
            return
        chat_id = kwargs.get("chat_id") or self.config.get("chat_id", "")
        if not chat_id:
            self.logger.warning("未配置 Chat ID，无法发送消息")
            return
        image: str = kwargs.get("image", "") or ""
        # image_bytes：聚合海报 PNG 字节（如后备搜索九宫格），优先级高于单图 URL
        image_bytes: Optional[bytes] = kwargs.get("image_bytes")

        # ── 正文格式约定 ──
        # 入参 text 一律是**标准 Markdown**（由 to_markdown / render_progress_text
        # 等上游产出），title 是纯文本。平台专属的转义只在这里做一次：
        #   md_body    → 标准 Markdown，直接喂富消息的 markdown 字段
        #   caption    → MarkdownV2，喂 send_photo/sendMessage 的 parse_mode
        #   plain_*    → 纯文本兜底，解析失败时用
        # why：过去 text 进来就已经是 MarkdownV2，等于把平台方言当成了模块间的
        # 传输格式，换发送方式就得反向还原一遍。现在统一为标准 Markdown 入、
        # 各自转义出，新增发送方式不必再关心历史包袱。
        md_body = f"**{title}**\n{text}" if title else text
        caption = self._markdown_to_v2(md_body)
        plain_caption = self._strip_markdown_v2(caption)
        # edit_message_id：有则 edit 已有消息，无则发新消息
        edit_message_id: Optional[int] = kwargs.get("edit_message_id")
        # _msg_id_out：调用方传入的列表，发新消息后把 message_id 写进去
        msg_id_out: Optional[list] = kwargs.get("_msg_id_out")
        # reply_markup：内联键盘按钮（列表格式同 CommandResult.reply_markup）
        raw_markup = kwargs.get("reply_markup")
        markup = self._build_inline_markup(raw_markup) if raw_markup else None
        # image_separate：图片模式 — 图片与文字分两条消息发送。
        # why: 先单独发图（无 caption），再走下方纯文本分支发文字，
        # 观感与企业微信的「图片模式」一致。
        if kwargs.get("image_separate") and (image or image_bytes) and not edit_message_id:
            try:
                if image_bytes:
                    import io as _sep_io
                    _photo = _sep_io.BytesIO(image_bytes)
                    _photo.name = "poster.png"
                    await asyncio.to_thread(self._bot.send_photo, chat_id, _photo)
                else:
                    await asyncio.to_thread(self._bot.send_photo, chat_id, image)
            except Exception as sep_err:
                self.logger.warning(f"图片模式单独发图失败，改为仅发文本: {sep_err}")
            # 图片已单独发出，后续按纯文本处理
            image = ""
            image_bytes = None

        try:
            # 仅当"纯文本编辑"时才走 edit_message_text（如任务进度消息反复刷新同一条）。
            # 若同时带图（image/image_bytes，如刷新完成的海报通知），则不能走此分支：
            # Telegram 无法把纯文本消息 edit 成图片消息，需改为"先删旧消息再发新图"，
            # 落入下方 image_bytes / image 分支处理。
            if edit_message_id and not (image or image_bytes):
                # 优先富消息（标准 Markdown 直传，进度条的 `[███░░]` 45% 无需转义）；
                # 不可用时回落到 MarkdownV2 带重试编辑
                success = await self._rich_edit(
                    chat_id, edit_message_id, md_body, markup=markup
                )
                if not success:
                    success = await self._edit_with_retry(
                        chat_id, edit_message_id, caption,
                        markup=markup, parse_mode="MarkdownV2",
                    )
                if not success:
                    # 重试全部失败，降级为发新消息（纯文本，不带 parse_mode）
                    self.logger.warning(f"edit_message_text 重试全部失败，降级为发纯文本新消息")
                    sent = await asyncio.to_thread(
                        self._bot.send_message, chat_id, plain_caption,
                        reply_markup=markup,
                    )
                    if msg_id_out is not None and sent:
                        msg_id_out.append(sent.message_id)
            elif image_bytes:
                # 聚合海报（PNG bytes）：以图片消息发送，正文作为 caption。
                # 失败时降级为纯文本，确保通知必达。
                # why：若带 edit_message_id（完成消息取代原进度消息），先删旧进度消息，
                # 因为图片消息无法由文本消息 edit 而来，只能"先删后发"。
                if edit_message_id:
                    try:
                        await asyncio.to_thread(
                            self._bot.delete_message, chat_id, edit_message_id
                        )
                    except Exception as del_err:
                        self.logger.debug(f"删除旧进度消息失败（忽略）: {del_err}")
                import io as _io
                try:
                    photo = _io.BytesIO(image_bytes)
                    photo.name = "poster.png"
                    sent = await asyncio.to_thread(
                        self._bot.send_photo, chat_id, photo, caption=caption,
                        parse_mode="MarkdownV2", reply_markup=markup,
                    )
                except Exception as photo_err:
                    photo_err_str = str(photo_err).lower()
                    if "can't parse entities" in photo_err_str:
                        self.logger.warning(f"send_photo(bytes) MarkdownV2 解析失败，降级纯文本caption: {photo_err}")
                        photo = _io.BytesIO(image_bytes)
                        photo.name = "poster.png"
                        sent = await asyncio.to_thread(
                            self._bot.send_photo, chat_id, photo,
                            caption=plain_caption, reply_markup=markup,
                        )
                    else:
                        self.logger.warning(f"send_photo(bytes) 失败，降级为纯文本消息: {photo_err}")
                        sent = await asyncio.to_thread(
                            self._bot.send_message, chat_id, caption,
                            parse_mode="MarkdownV2", reply_markup=markup,
                        )
                if msg_id_out is not None and sent:
                    msg_id_out.append(sent.message_id)
            elif image:
                # 有封面图（HTTPS URL）：优先富消息，图片以媒体块内嵌到正文顶部。
                # why：若带 edit_message_id（完成消息取代原进度消息），先删旧进度消息，
                # 因为图片消息无法由文本消息 edit 而来，只能"先删后发"。
                if edit_message_id:
                    try:
                        await asyncio.to_thread(
                            self._bot.delete_message, chat_id, edit_message_id
                        )
                    except Exception as del_err:
                        self.logger.debug(f"删除旧进度消息失败（忽略）: {del_err}")
                # 富消息只支持 HTTP(S) URL 的媒体（无 multipart 通道），
                # 非 http 开头的地址（如本地路径）仍走 send_photo
                if image.startswith("http"):
                    sent = await self._rich_send(
                        chat_id, md_body, image_url=image, markup=markup
                    )
                    if sent:
                        if msg_id_out is not None:
                            msg_id_out.append(sent.message_id)
                        return
                try:
                    sent = await asyncio.to_thread(self._bot.send_photo, chat_id, image, caption=caption, parse_mode="MarkdownV2", reply_markup=markup)
                except Exception as photo_err:
                    photo_err_str = str(photo_err).lower()
                    if "can't parse entities" in photo_err_str:
                        # MarkdownV2 语法错误：图片能发，只是 caption 解析失败，降级纯文本 caption
                        self.logger.warning(f"send_photo MarkdownV2 解析失败，降级为纯文本 caption: {photo_err}")
                        sent = await asyncio.to_thread(self._bot.send_photo, chat_id, image, caption=plain_caption, reply_markup=markup)
                    else:
                        # 图片 URL 不可访问（如 HTTP 地址被 TG 拒绝）或其他网络错误：
                        # 图片发不出去，降级为发纯文字消息，不再 raise 让外层兜底。
                        # why：外层 except 的 _strip_markdown_v2 不处理 [text](url) 链接语法，
                        # 会把 [海报](URL) 原样打印到消息里（TG 纯文本不渲染 Markdown 链接）。
                        # 改为就地降级，plain_caption 已经过 _strip_markdown_v2 完整清洗。
                        self.logger.warning(f"send_photo 图片发送失败，降级为纯文字消息（图片 URL 可能不可访问）: {photo_err}")
                        try:
                            # 尝试发送带 markup 的纯文本消息
                            sent = await asyncio.to_thread(self._bot.send_message, chat_id, plain_caption, reply_markup=markup)
                        except Exception as text_err:
                            # markup 中可能也包含不可访问的 URL（如按钮链接），最后降级为无 markup 的纯文本
                            self.logger.warning(f"带 markup 的纯文本消息也失败，移除 markup 重试: {text_err}")
                            sent = await asyncio.to_thread(self._bot.send_message, chat_id, plain_caption)
                if msg_id_out is not None and sent:
                    msg_id_out.append(sent.message_id)
            else:
                # 无图纯文本：优先富消息（标准 Markdown 直传，表格/标题原生渲染）
                sent = await self._rich_send(chat_id, md_body, markup=markup)
                if sent:
                    if msg_id_out is not None:
                        msg_id_out.append(sent.message_id)
                    return
                try:
                    sent = await asyncio.to_thread(self._bot.send_message, chat_id, caption, parse_mode="MarkdownV2", reply_markup=markup)
                except Exception as send_err:
                    send_err_str = str(send_err).lower()
                    if "can't parse entities" in send_err_str:
                        self.logger.warning(f"send_message MarkdownV2 解析失败，降级为纯文本: {send_err}")
                        sent = await asyncio.to_thread(self._bot.send_message, chat_id, plain_caption, reply_markup=markup)
                    else:
                        raise
                if msg_id_out is not None and sent:
                    msg_id_out.append(sent.message_id)
        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")
            # 降级为纯文本。直接复用上面算好的 plain_caption，
            # 它是 _strip_markdown_v2(_markdown_to_v2(md_body)) 的产物——
            # why：不能对原始 text 直接 strip，那样表格的 | 和 |---| 会原样留下
            # （_strip_markdown_v2 不认表格，表格降级在 _markdown_to_v2 里）。
            try:
                sent = await asyncio.to_thread(
                    self._bot.send_message, chat_id, plain_caption
                )
                if msg_id_out is not None and sent:
                    msg_id_out.append(sent.message_id)
            except Exception:
                pass

    def render_progress_text(self, progress: int, description: str) -> str:
        """渲染进度条，产出**标准 Markdown**。

        why：本方法过去直接产出 MarkdownV2（用 _escape_markdown_v2 转义百分号和
        描述里的 "."），导致 MarkdownV2 这个平台专属方言泄漏成了模块间的传输格式，
        下游想改用别的发送方式就得先反向还原一遍。
        现在统一约定：**上游一律产出标准 Markdown，转义只在 send_message 里
        按目标 API 做一次**。进度条本体用反引号包裹（█░ 无需转义），
        百分比和描述直接写，交由发送层处理。
        """
        filled = max(0, min(10, int(progress / 10)))
        bar = "█" * filled + "░" * (10 - filled)
        return f"`[{bar}]` {progress}%\n• {description}"

    async def send_quick(self, text: str, chat_id=None) -> Optional[int]:
        """发送一条快速消息，返回 message_id 供后续 edit 使用"""
        if not self._bot:
            return None
        target = chat_id or self.config.get("chat_id", "")
        if not target:
            return None
        try:
            sent = await asyncio.to_thread(self._bot.send_message, target, text)
            return sent.message_id if sent else None
        except Exception as e:
            self.logger.warning(f"send_quick 失败: {e}")
            return None

    async def test_connection(self) -> Dict[str, Any]:
        bot_token = self.config.get("bot_token", "")
        if not bot_token:
            return {"success": False, "message": "Bot Token 未配置"}
        try:
            telebot = _get_telebot()
            # 测试时同样应用代理/出网代理配置
            api_proxy = self.config.get("telegram_api_proxy", "").strip().rstrip("/")
            if api_proxy:
                telebot.apihelper.API_URL = f"{api_proxy}/out/api.telegram.org/bot{{0}}/{{1}}"
                telebot.apihelper.proxy = None
            elif self.proxy_url:
                telebot.apihelper.proxy = {"https": self.proxy_url}
                telebot.apihelper.API_URL = "https://api.telegram.org/bot{0}/{1}"
            else:
                telebot.apihelper.proxy = None
                telebot.apihelper.API_URL = "https://api.telegram.org/bot{0}/{1}"
            telebot.apihelper.CONNECT_TIMEOUT = 10
            telebot.apihelper.READ_TIMEOUT = 15
            bot = telebot.TeleBot(bot_token, threaded=False)
            info = await asyncio.to_thread(bot.get_me)
            # 发送测试消息到配置的 chat_id
            chat_id = self.config.get("chat_id", "")
            if chat_id:
                try:
                    await asyncio.to_thread(
                        bot.send_message,
                        chat_id,
                        f"🔔 测试连接成功！\nBot: @{info.username} ({info.first_name})\n来自 Misaka 弹幕服务器的测试消息。\n版本：v{APP_VERSION}",
                    )
                except Exception as e:
                    self.logger.warning(f"测试消息发送失败: {e}")
            return {
                "success": True,
                "message": f"连接成功！Bot: @{info.username} ({info.first_name})" + (f"，测试消息已发送到 {chat_id}" if chat_id else ""),
                "botInfo": {"username": info.username, "firstName": info.first_name, "id": info.id},
            }
        except Exception as e:
            return {"success": False, "message": f"连接失败: {e}"}

    def process_webhook_update(self, update_json: dict) -> bool:
        """处理 Webhook 推送的 update（由通用 webhook 回调路由调用）"""
        if not self._bot:
            return False
        if self.config.get("mode") != "webhook":
            return False
        telebot = _get_telebot()
        update = telebot.types.Update.de_json(update_json)
        self._bot.process_new_updates([update])
        return True
