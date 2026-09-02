"""
QQ 官方 Bot 通知渠道实现（v2 API）
使用 QQ 官方机器人 OpenAPI v2，支持单聊（C2C）和群聊消息发送。
参考：MoviePilot 项目架构 + QQ 官方文档
官方文档：https://bot.q.qq.com/wiki/develop/api-v2/
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

from src.notification.base import (
    BaseNotificationChannel,
    ChannelCapability, ChannelCapabilities, IMAGE_MODE_FIELD,
    IMAGE_MODE_TEXT, IMAGE_MODE_PUBLIC_URL,
)

logger = logging.getLogger(__name__)

# QQ Bot OpenAPI v2 基地址
QQ_BOT_API_BASE = "https://api.sgroup.qq.com"
QQ_BOT_SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"


class QQBotChannel(BaseNotificationChannel):
    """QQ 官方 Bot 通知渠道 — 支持单聊（C2C）和群聊消息"""

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
        self.app_secret = config.get("app_secret", "")
        self.is_sandbox = config.get("is_sandbox", False)

        # 用户 OpenID（单聊）和群组 OpenID（群聊）二选一
        self.user_openid = config.get("user_openid", "")
        self.group_openid = config.get("group_openid", "")

        # 管理员白名单（可选）
        self.admin_whitelist = config.get("admin_whitelist", "")

        # HTTP 客户端
        self.http_client: Optional[httpx.AsyncClient] = None
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    @property
    def api_base(self) -> str:
        """获取 API 基地址"""
        return QQ_BOT_SANDBOX_API_BASE if self.is_sandbox else QQ_BOT_API_BASE

    async def _ensure_http_client(self):
        """确保 HTTP 客户端已初始化"""
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=30.0)

    async def _get_access_token(self) -> Optional[str]:
        """获取 Access Token（使用 App 鉴权）"""
        import time

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
                    "clientSecret": self.app_secret,
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

    async def _send_qq_message(
        self,
        content: Optional[str] = None,
        markdown: Optional[Dict] = None,
        keyboard: Optional[Dict] = None,
        image_url: Optional[str] = None,
        msg_id: Optional[str] = None,
        openid: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        发送消息到单聊或群聊（底层实现）

        Args:
            content: 消息内容（纯文本）
            markdown: Markdown 消息（可选）
            keyboard: 消息按钮（可选）
            image_url: 图片URL（可选）
            msg_id: 要回复的消息ID（可选）
            openid: 目标 OpenID（不传则使用配置的默认值）
        """
        token = await self._get_access_token()
        if not token:
            logger.error("无法获取 access_token")
            return None

        await self._ensure_http_client()

        # 确定目标 openid（单聊或群聊）
        target_openid = openid or self.user_openid or self.group_openid
        if not target_openid:
            logger.error("QQ Bot 发送失败：未配置用户 OpenID 或群组 OpenID")
            return None

        # 判断是单聊还是群聊
        is_group = bool(openid and openid == self.group_openid) or (not openid and self.group_openid and not self.user_openid)

        # 构建 API 路径
        if is_group:
            url = f"{self.api_base}/v2/groups/{target_openid}/messages"
        else:
            url = f"{self.api_base}/v2/users/{target_openid}/messages"

        # 构建消息体
        payload: Dict[str, Any] = {
            "msg_type": 0,  # 文本消息
            "msg_id": msg_id or "",
        }

        if content:
            payload["content"] = content
        if markdown:
            payload["markdown"] = markdown
        if keyboard:
            payload["keyboard"] = keyboard
        if image_url:
            payload["media"] = {"file_info": image_url}

        headers = {
            "Authorization": f"QQBot {self.app_id}.{token}",
            "Content-Type": "application/json",
        }

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

        # 调用底层 HTTP API 发送
        await self._send_qq_message(
            content=full_text,
            keyboard=keyboard,
            image_url=image_url,
            msg_id=msg_id,
            openid=openid,
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
            token = await self._get_access_token()
            if not token:
                return {"success": False, "message": "无法获取 access_token"}

            # 尝试获取机器人信息
            await self._ensure_http_client()
            headers = {
                "Authorization": f"QQBot {self.app_id}.{token}",
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

    async def start(self):
        """启动渠道"""
        await super().start()
        logger.info(f"QQ Bot 渠道已启动: {self.name}")

    async def stop(self):
        """停止渠道"""
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


