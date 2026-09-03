"""
QQ 官方 Bot 通知渠道实现（WebSocket Gateway）
使用 qq-botpy SDK，通过 WebSocket 接收消息和事件，支持双向交互。
参考：MoviePilot 项目架构 + QQ 官方文档
官方文档：https://bot.q.qq.com/wiki/
"""

import logging
import threading
from typing import Any, Dict, List, Optional

from src.notification.base import (
    BaseNotificationChannel, CommandResult,
    ChannelCapability, ChannelCapabilities, IMAGE_MODE_FIELD,
    IMAGE_MODE_TEXT, IMAGE_MODE_PUBLIC_URL,
)

logger = logging.getLogger(__name__)
bot_raw_logger = logging.getLogger("bot_raw")


def _get_botpy():
    """延迟导入 botpy，避免未安装时影响启动"""
    try:
        import botpy
        return botpy
    except ImportError:
        raise ImportError("请安装 qq-botpy: pip install qq-botpy")


def _get_markdown_payload():
    """延迟导入 MarkdownPayload"""
    try:
        from botpy.types.message import MarkdownPayload
        return MarkdownPayload
    except ImportError:
        logger.warning("无法导入 MarkdownPayload，将使用纯文本格式")
        return None


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

        # Bot 客户端实例
        self._bot_client = None
        self._bot_thread: Optional[threading.Thread] = None
        self._should_stop = False

        # 事件循环（用于异步操作）
        self._event_loop = None

    def _is_log_raw(self) -> bool:
        """检查是否启用原始日志"""
        return str(self.config.get("log_raw", "false")).lower() == "true"

    def _log_raw(self, direction: str, data):
        """记录原始交互日志"""
        if self._is_log_raw():
            import json
            bot_raw_logger.info(
                f"[QQ Bot #{self.channel_id}] {direction}\n"
                f"{json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else data}\n"
                f"{'─' * 60}"
            )

    def _start_bot_client(self):
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

                    # 注册菜单命令
                    await self.parent_channel._register_commands()

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

            # 在新线程中启动 Bot
            def run_bot():
                try:
                    # botpy 的 run() 方法会自动创建和管理事件循环
                    # 不需要手动创建 event loop，否则会导致 "event loop is already running" 错误
                    self._bot_client.run(
                        appid=self.app_id,
                        secret=self.app_secret,
                    )
                except Exception as e:
                    logger.error(f"QQ Bot 运行异常: {e}")

            self._bot_thread = threading.Thread(target=run_bot, daemon=True)
            self._bot_thread.start()
            logger.info("QQ Bot WebSocket 客户端已启动")

        except Exception as e:
            logger.error(f"QQ Bot 启动失败: {e}")

    async def _handle_c2c_message(self, message):
        """处理单聊（C2C）消息"""
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
            # botpy 的 User 对象没有 username，直接使用 user_openid
            username = user_openid
            msg_id = message.id

            bot_raw_logger.info(f"收到QQ单聊消息: user={username}, content={content}")

            # 检查管理员权限（如果配置了白名单）
            if self.admin_whitelist:
                allowed_users = [u.strip() for u in self.admin_whitelist.split(",")]
                if user_openid not in allowed_users:
                    logger.warning(f"用户 {user_openid} 不在管理员白名单中，忽略消息")
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
        try:
            # 记录原始消息
            self._log_raw("⬇️ 收到群聊消息", {
                "message_id": message.id,
                "group_openid": message.group_openid,
                "user_openid": message.author.user_openid,
                "content": message.content,
                "timestamp": message.timestamp if hasattr(message, 'timestamp') else None,
            })

            content = message.content.strip()
            user_openid = message.author.user_openid
            group_openid = message.group_openid
            # botpy 的 User 对象没有 username，直接使用 user_openid
            username = user_openid
            msg_id = message.id

            bot_raw_logger.info(f"收到QQ群聊消息: group={group_openid}, user={username}, content={content}")

            # 检查管理员权限（如果配置了白名单）
            if self.admin_whitelist:
                allowed_users = [u.strip() for u in self.admin_whitelist.split(",")]
                if user_openid not in allowed_users:
                    logger.warning(f"用户 {user_openid} 不在管理员白名单中，忽略消息")
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
        """处理按钮交互回调"""
        try:
            callback_data = interaction.data.resolved.button_data
            user_openid = interaction.data.resolved.user_id

            bot_raw_logger.info(f"收到QQ按钮回调: user={user_openid}, data={callback_data}")

            # TODO: 处理按钮回调逻辑
            # 可以调用 notification_service.handle_callback() 或其他处理方法

        except Exception as e:
            logger.error(f"QQ Bot 处理按钮回调失败: {e}", exc_info=True)

    async def _send_c2c_message(
        self,
        user_openid: str,
        content: Optional[str] = None,
        keyboard: Optional[Dict] = None,
        image_url: Optional[str] = None,
        msg_id: Optional[str] = None,
        markdown: bool = True,
    ):
        """发送单聊消息（通过 botpy API）

        :param markdown: 是否使用 Markdown 格式（默认 True）
        """
        if not self._bot_client:
            logger.error("Bot 客户端未初始化")
            return

        try:
            MarkdownPayload = _get_markdown_payload()

            message_data = {}

            # 优先使用 Markdown 格式
            if content and markdown and MarkdownPayload:
                message_data["markdown"] = MarkdownPayload(content=content)
            elif content:
                message_data["content"] = content

            # 只在 keyboard 非空时才传递
            if keyboard and len(keyboard) > 0:
                message_data["keyboard"] = keyboard
            if image_url:
                message_data["image"] = image_url
            if msg_id:
                message_data["msg_id"] = msg_id

            # 记录发送请求
            self._log_raw("⬆️ 发送单聊消息", {
                "user_openid": user_openid,
                "message_data": message_data,
            })

            await self._bot_client.api.post_c2c_message(
                openid=user_openid,
                **message_data
            )

            # 记录发送成功
            self._log_raw("✅ 单聊消息发送成功", {
                "user_openid": user_openid,
            })

            logger.info(f"QQ Bot 单聊消息发送成功: user={user_openid}")
        except Exception as e:
            logger.error(f"QQ Bot 单聊消息发送失败: {e}", exc_info=True)

    async def _send_group_message(
        self,
        group_openid: str,
        content: Optional[str] = None,
        keyboard: Optional[Dict] = None,
        image_url: Optional[str] = None,
        msg_id: Optional[str] = None,
        markdown: bool = True,
    ):
        """发送群聊消息（通过 botpy API）

        :param markdown: 是否使用 Markdown 格式（默认 True）
        """
        if not self._bot_client:
            logger.error("Bot 客户端未初始化")
            return

        try:
            MarkdownPayload = _get_markdown_payload()

            message_data = {}

            # 优先使用 Markdown 格式
            if content and markdown and MarkdownPayload:
                message_data["markdown"] = MarkdownPayload(content=content)
            elif content:
                message_data["content"] = content

            # 只在 keyboard 非空时才传递
            if keyboard and len(keyboard) > 0:
                message_data["keyboard"] = keyboard
            if image_url:
                message_data["image"] = image_url
            if msg_id:
                message_data["msg_id"] = msg_id

            # 记录发送请求
            self._log_raw("⬆️ 发送群聊消息", {
                "group_openid": group_openid,
                "message_data": message_data,
            })

            await self._bot_client.api.post_group_message(
                group_openid=group_openid,
                **message_data
            )

            # 记录发送成功
            self._log_raw("✅ 群聊消息发送成功", {
                "group_openid": group_openid,
            })

            logger.info(f"QQ Bot 群聊消息发送成功: group={group_openid}")
        except Exception as e:
            logger.error(f"QQ Bot 群聊消息发送失败: {e}", exc_info=True)

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
            return

        # 判断是单聊还是群聊
        is_group = (openid == self.group_openid) if openid else (self.group_openid and not self.user_openid)

        # 发送消息
        if is_group:
            await self._send_group_message(
                group_openid=target_openid,
                content=full_text,
                keyboard=keyboard,
                image_url=image_url,
                msg_id=msg_id,
            )
        else:
            await self._send_c2c_message(
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

        for row_buttons in reply_markup:
            button_row = []
            for btn in row_buttons:
                button_obj = {
                    "id": btn.get("callback_data", btn["text"])[:20],
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
        # 启动 Bot WebSocket 客户端
        self._start_bot_client()

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
            except:
                pass

        await super().stop()
        logger.info(f"QQ Bot 渠道已停止: {self.name}")

    async def _register_commands(self):
        """注册菜单命令到 QQ Bot"""
        try:
            if not self._bot_client:
                logger.warning("[QQ Bot] Bot 客户端未初始化，无法注册命令")
                return

            # 从 NotificationService 获取所有命令
            commands = self.service.get_available_commands()
            if not commands:
                logger.info("[QQ Bot] 没有可注册的命令")
                return

            # 构建命令列表（QQ Bot API 格式）
            command_list = []
            for cmd in commands:
                command_list.append({
                    "name": cmd.get("command", ""),
                    "description": cmd.get("description", ""),
                })

            if not command_list:
                logger.info("[QQ Bot] 命令列表为空，跳过注册")
                return

            # 调用 QQ Bot API 注册命令
            try:
                # botpy 的命令注册 API（需要使用 api 对象）
                # 参考：https://bot.q.qq.com/wiki/develop/api/openapi/setting/commands_setting.html
                api = self._bot_client.api
                guild_id = None  # 全局命令设置为 None

                await api.set_commands(
                    guild_id=guild_id,
                    commands=command_list,
                )

                logger.info(f"[QQ Bot] 菜单命令注册成功: {len(command_list)} 条命令")
                for cmd in command_list:
                    logger.info(f"  - /{cmd['name']}: {cmd['description']}")

            except Exception as e:
                logger.error(f"[QQ Bot] 命令注册 API 调用失败: {e}")
                logger.info("[QQ Bot] 请手动在 QQ 开放平台后台配置以下命令：")
                for cmd in command_list:
                    logger.info(f"  - /{cmd['name']}: {cmd['description']}")

        except Exception as e:
            logger.error(f"[QQ Bot] 命令注册失败: {e}", exc_info=True)

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

    async def _download_photo_data_url(self, file_id: str) -> Optional[str]:
        """
        把 QQ 图片下载为 data URL（base64），供 vision 模型识别。

        :param file_id: QQ 图片的 file_id
        :return: data URL 或 None（失败时）
        """
        try:
            if not self._bot_client:
                return None

            # QQ Bot API 获取文件下载 URL
            file_info = await self._bot_client.api.get_file_info(file_id)
            if not file_info or not file_info.get("url"):
                logger.warning(f"[QQ 图片] 无法获取文件信息: file_id={file_id}")
                return None

            file_url = file_info["url"]

            # 下载图片
            import aiohttp
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
                    import base64
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
                # QQ Bot API 的图片附件结构
                if attach.get("content_type", "").startswith("image/"):
                    file_id = attach.get("id") or attach.get("file_id")
                    if file_id:
                        data_url = await self._download_photo_data_url(file_id)
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

