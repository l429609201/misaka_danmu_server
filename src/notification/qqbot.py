"""
QQ 官方 Bot 通知渠道实现
使用 qq-botpy 官方 SDK，支持 WebSocket Gateway 和 HTTP API。
支持双向交互：命令处理、消息接收、富文本等。
参考：MoviePilot 项目架构 + QQ官方文档
官方文档：https://bot.q.qq.com/wiki/
"""

import asyncio
import json
import logging
import threading
from typing import Any, Dict, List, Optional

import httpx

from src.notification.base import (
    BaseNotificationChannel, CommandResult,
    ChannelCapability, ChannelCapabilities, IMAGE_MODE_FIELD,
    IMAGE_MODE_TEXT, IMAGE_MODE_POSTER, IMAGE_MODE_SEPARATE, IMAGE_MODE_PUBLIC_URL,
)
from src._version import APP_VERSION

logger = logging.getLogger(__name__)
bot_raw_logger = logging.getLogger("bot_raw")

# QQ Bot OpenAPI 基地址
QQ_BOT_API_BASE = "https://api.sgroup.qq.com"
QQ_BOT_SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"


def _get_botpy():
    """延迟导入 botpy，避免未安装时影响启动"""
    try:
        import botpy
        return botpy
    except ImportError:
        raise ImportError("请安装 qq-botpy: pip install qq-botpy")


class QQBotChannel(BaseNotificationChannel):
    """QQ 官方 Bot 通知渠道 — 支持双向交互"""

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
        },
    )

    def __init__(self, channel_id: int, name: str, config: dict, notification_service):
        super().__init__(channel_id, name, config, notification_service)
        
        self.app_id = config.get("app_id", "")
        self.client_secret = config.get("client_secret", "")
        self.bot_token = config.get("bot_token", "")
        self.is_sandbox = config.get("is_sandbox", False)
        self.target_channel_id = config.get("channel_id", "")  # 默认发送的频道ID
        
        # HTTP 客户端
        self.http_client: Optional[httpx.AsyncClient] = None
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        
        # Bot 实例（用于接收消息）
        self._bot_client = None
        self._bot_thread: Optional[threading.Thread] = None
        self._should_stop = False

    @property
    def api_base(self) -> str:
        """获取 API 基地址"""
        return QQ_BOT_SANDBOX_API_BASE if self.is_sandbox else QQ_BOT_API_BASE

    async def _ensure_http_client(self):
        """确保 HTTP 客户端已初始化"""
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=30.0)

    async def _get_access_token(self) -> Optional[str]:
        """获取 Access Token（使用 Bot Token 或 App 鉴权）"""
        import time
        
        # 如果有 bot_token，直接使用
        if self.bot_token:
            return self.bot_token
        
        # 检查缓存的 token 是否有效
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        
        # 获取新 token
        await self._ensure_http_client()
        try:
            response = await self.http_client.post(
                f"{self.api_base}/app/getAppAccessToken",
                json={
                    "appId": self.app_id,
                    "clientSecret": self.client_secret,
                }
            )
            response.raise_for_status()
            data = response.json()
            
            self._access_token = data.get("access_token")
            expires_in = data.get("expires_in", 7200)
            self._token_expires_at = time.time() + expires_in - 300  # 提前5分钟刷新
            
            logger.info(f"QQ Bot 获取 access_token 成功，有效期 {expires_in} 秒")
            return self._access_token
            
        except Exception as e:
            logger.error(f"QQ Bot 获取 access_token 失败: {e}")
            return None

    async def send_message(self, channel_id: str, content: str,
                          msg_id: Optional[str] = None,
                          keyboard: Optional[Dict] = None,
                          markdown: Optional[Dict] = None,
                          image_url: Optional[str] = None) -> Optional[Dict]:
        """
        发送消息到指定频道

        Args:
            channel_id: 频道ID
            content: 消息内容（纯文本）
            msg_id: 要回复的消息ID（可选）
            keyboard: 消息按钮（可选）
            markdown: Markdown 消息（可选）
            image_url: 图片URL（可选）
        """
        token = await self._get_access_token()
        if not token:
            logger.error("QQ Bot 发送消息失败：无法获取 access_token")
            return None

        await self._ensure_http_client()

        # 构建消息体
        message_body: Dict[str, Any] = {}

        # 优先使用 Markdown
        if markdown:
            message_body["markdown"] = markdown
        elif content:
            message_body["content"] = content

        # 添加图片（如果有）
        if image_url:
            message_body["image"] = image_url

        # 添加按钮（如果有）
        if keyboard:
            message_body["keyboard"] = keyboard

        # 引用消息
        if msg_id:
            message_body["msg_id"] = msg_id

        headers = {
            "Authorization": f"Bot {self.app_id}.{token}",
            "Content-Type": "application/json",
        }

        try:
            response = await self.http_client.post(
                f"{self.api_base}/channels/{channel_id}/messages",
                json=message_body,
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()

            bot_raw_logger.info(f"QQ Bot 发送消息成功: {result}")
            return result

        except Exception as e:
            logger.error(f"QQ Bot 发送消息失败: {e}")
            return None

    def _start_bot_client(self):
        """启动 Bot WebSocket 客户端（用于接收消息和事件）"""
        if not self.app_id or not self.bot_token:
            logger.warning("QQ Bot 配置不完整，无法启动消息接收")
            return

        try:
            botpy = _get_botpy()

            # 创建 Bot 客户端
            class MessageBot(botpy.Client):
                def __init__(self, parent_channel: 'QQBotChannel'):
                    super().__init__()
                    self.parent_channel = parent_channel

                async def on_ready(self):
                    logger.info(f"QQ Bot 已连接: {self.robot.name}")

                async def on_at_message_create(self, message: botpy.message.Message):
                    """处理 @机器人 消息"""
                    await self.parent_channel._handle_message(message)

                async def on_direct_message_create(self, message: botpy.message.DirectMessage):
                    """处理私信消息"""
                    await self.parent_channel._handle_direct_message(message)

            self._bot_client = MessageBot(self)

            # 在新线程中启动 Bot
            def run_bot():
                try:
                    intents = botpy.Intents(public_messages=True, direct_message=True)
                    self._bot_client.run(appid=self.app_id, secret=self.bot_token, intents=intents)
                except Exception as e:
                    logger.error(f"QQ Bot 运行异常: {e}")

            self._bot_thread = threading.Thread(target=run_bot, daemon=True)
            self._bot_thread.start()
            logger.info("QQ Bot WebSocket 客户端已启动")

        except Exception as e:
            logger.error(f"QQ Bot 启动失败: {e}")

    async def _handle_message(self, message):
        """处理频道消息"""
        try:
            content = message.content.strip()
            user_id = message.author.id
            username = message.author.username
            channel_id = message.channel_id
            msg_id = message.id

            bot_raw_logger.info(f"收到QQ频道消息: user={username}({user_id}), content={content}")

            # 处理命令
            result = await self.notification_service.handle_command(
                user_id=user_id,
                username=username,
                text=content,
                channel=self,
            )

            # 发送回复
            if result and result.reply_text:
                await self.send_message(
                    channel_id=channel_id,
                    content=result.reply_text,
                    msg_id=msg_id,
                )

        except Exception as e:
            logger.error(f"QQ Bot 处理消息失败: {e}")

    async def _handle_direct_message(self, message):
        """处理私信消息"""
        try:
            content = message.content.strip()
            user_id = message.author.id
            username = message.author.username
            guild_id = message.guild_id

            bot_raw_logger.info(f"收到QQ私信: user={username}({user_id}), content={content}")

            # 处理命令
            result = await self.notification_service.handle_command(
                user_id=user_id,
                username=username,
                text=content,
                channel=self,
            )

            # 发送私信回复
            if result and result.reply_text:
                await self._send_direct_message(guild_id, user_id, content=result.reply_text)

        except Exception as e:
            logger.error(f"QQ Bot 处理私信失败: {e}")


    async def _send_direct_message(self, guild_id: str, user_id: str,
                                   content: str = "", markdown: Optional[Dict] = None) -> Optional[Dict]:
        """发送私信消息"""
        token = await self._get_access_token()
        if not token:
            return None

        await self._ensure_http_client()

        message_body = {}
        if markdown:
            message_body["markdown"] = markdown
        elif content:
            message_body["content"] = content

        headers = {
            "Authorization": f"Bot {self.app_id}.{token}",
            "Content-Type": "application/json",
        }

        try:
            response = await self.http_client.post(
                f"{self.api_base}/dms/{guild_id}/messages",
                json=message_body,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"QQ Bot 发送私信失败: {e}")
            return None

    async def _send_qq_message(
        self,
        channel_id: str,
        content: Optional[str] = None,
        markdown: Optional[Dict] = None,
        keyboard: Optional[Dict] = None,
        image_url: Optional[str] = None,
        msg_id: Optional[str] = None,
    ):
        """通过 HTTP API 发送 QQ 消息（底层实现）"""
        token = await self._get_access_token()
        if not token:
            logger.error("无法获取 access_token")
            return

        await self._ensure_http_client()

        # 构建请求
        url = f"{self.api_base}/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {self.app_id}.{token}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {}
        if content:
            payload["content"] = content
        if markdown:
            payload["markdown"] = markdown
        if keyboard:
            payload["keyboard"] = keyboard
        if image_url:
            payload["image"] = image_url
        if msg_id:
            payload["msg_id"] = msg_id

        try:
            response = await self.http_client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            logger.info(f"QQ Bot 消息发送成功: {result.get('id')}")
            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"QQ Bot 消息发送失败: HTTP {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"QQ Bot 消息发送异常: {e}")
            raise

    # ========== BaseNotificationChannel 实现 ==========

    async def send_message(self, title: str, text: str, **kwargs):
        """发送消息（BaseNotificationChannel 要求实现）"""
        channel_id = self.target_channel_id or kwargs.get("channel_id")
        if not channel_id:
            logger.error("QQ Bot 发送失败：未配置目标频道ID")
            return

        # 提取参数
        image = kwargs.get("image")
        image_bytes = kwargs.get("image_bytes")
        reply_markup = kwargs.get("reply_markup", [])
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

        # 调用底层 HTTP API 发送
        await self._send_qq_message(
            channel_id=channel_id,
            content=full_text,
            keyboard=keyboard,
            image_url=image_url,
            msg_id=msg_id,
        )

    async def test_connection(self) -> Dict[str, Any]:
        """测试连接"""
        try:
            token = await self._get_access_token()
            if not token:
                return {"success": False, "message": "无法获取 access_token"}

            # 尝试获取频道信息
            await self._ensure_http_client()
            headers = {
                "Authorization": f"Bot {self.app_id}.{token}",
            }

            response = await self.http_client.get(
                f"{self.api_base}/users/@me",
                headers=headers,
            )
            response.raise_for_status()
            bot_info = response.json()

            return {
                "success": True,
                "message": f"连接成功！Bot: {bot_info.get('username', 'Unknown')}"
            }
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

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

    def _build_keyboard(self, buttons: List[Dict]) -> Dict:
        """构建 QQ Bot 按钮格式（旧接口兼容）"""
        rows = []
        current_row = []

        for btn in buttons:
            button_obj = {
                "id": btn.get("id", btn["text"][:10]),
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

            current_row.append(button_obj)

            # QQ Bot 每行最多 5 个按钮
            if len(current_row) >= 5:
                rows.append({"buttons": current_row})
                current_row = []

        if current_row:
            rows.append({"buttons": current_row})

        return {"content": {"rows": rows}}

    async def reply(self, user_id: str, text: str, buttons: Optional[List[Dict]] = None):
        """回复用户消息（兼容接口，user_id 用于标识用户但当前实现发到默认频道）"""
        channel_id = self.target_channel_id
        if channel_id:
            keyboard = self._build_keyboard(buttons) if buttons else None
            await self._send_qq_message(
                channel_id=channel_id,
                content=text,
                keyboard=keyboard,
            )

    async def start(self):
        """启动渠道"""
        await super().start()
        # 启动 Bot 客户端接收消息
        self._start_bot_client()
        logger.info(f"QQ Bot 渠道已启动: {self.name}")

    async def stop(self):
        """停止渠道"""
        self._should_stop = True
        if self._bot_client:
            try:
                await self._bot_client.close()
            except:
                pass
        if self.http_client:
            await self.http_client.aclose()
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
                "required": True,
                "description": "QQ Bot 的 App ID（机器人ID）",
                "description_en": "QQ Bot App ID (Bot ID)",
                "description_tw": "QQ Bot 的 App ID（機器人ID）",
            },
            {
                "key": "bot_token",
                "label": "Bot Token",
                "label_en": "Bot Token",
                "label_tw": "Bot Token",
                "type": "password",
                "required": True,
                "description": "QQ Bot 的机器人令牌（在 QQ 开放平台获取）",
                "description_en": "QQ Bot Token (Get from QQ Open Platform)",
                "description_tw": "QQ Bot 的機器人令牌（在 QQ 開放平台獲取）",
            },
            {
                "key": "client_secret",
                "label": "Client Secret",
                "label_en": "Client Secret",
                "label_tw": "Client Secret",
                "type": "password",
                "required": False,
                "description": "应用密钥（使用 App 鉴权时需要）",
                "description_en": "Client Secret (Required for App authentication)",
                "description_tw": "應用密鑰（使用 App 鑑權時需要）",
            },
            {
                "key": "channel_id",
                "label": "默认频道ID",
                "label_en": "Default Channel ID",
                "label_tw": "預設頻道ID",
                "type": "string",
                "required": True,
                "description": "接收通知的频道ID（右键频道 → 复制频道ID）",
                "description_en": "Channel ID for receiving notifications (Right-click channel → Copy Channel ID)",
                "description_tw": "接收通知的頻道ID（右鍵頻道 → 複製頻道ID）",
            },
            {
                "key": "is_sandbox",
                "label": "沙箱模式",
                "label_en": "Sandbox Mode",
                "label_tw": "沙盒模式",
                "type": "boolean",
                "default": False,
                "description": "是否使用沙箱环境（测试用）",
                "description_en": "Use sandbox environment (for testing)",
                "description_tw": "是否使用沙盒環境（測試用）",
            },
            IMAGE_MODE_FIELD,
        ]

