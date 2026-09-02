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

            # 处理命令（注意：父类中存储为 self.service，不是 self.notification_service）
            result = await self.service.handle_command(
                user_id=user_openid,
                username=username,
                text=content,
                channel=self,
            )

            # 发送回复
            if result and result.reply_text:
                await self._send_c2c_message(
                    user_openid=user_openid,
                    content=result.reply_text,
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

            # 处理命令（注意：父类中存储为 self.service，不是 self.notification_service）
            result = await self.service.handle_command(
                user_id=user_openid,
                username=username,
                text=content,
                channel=self,
            )

            # 发送回复
            if result and result.reply_text:
                await self._send_group_message(
                    group_openid=group_openid,
                    content=result.reply_text,
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
    ):
        """发送单聊消息（通过 botpy API）"""
        if not self._bot_client:
            logger.error("Bot 客户端未初始化")
            return

        try:
            message_data = {}
            if content:
                message_data["content"] = content
            if keyboard:
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
    ):
        """发送群聊消息（通过 botpy API）"""
        if not self._bot_client:
            logger.error("Bot 客户端未初始化")
            return

        try:
            message_data = {}
            if content:
                message_data["content"] = content
            if keyboard:
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
        logger.info(f"QQ Bot 渠道已启动: {self.name}")

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


