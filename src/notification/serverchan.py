"""
Server酱³ (ServerChan3) Bot 通知渠道实现
使用 SC3 Bot API（类 Telegram Bot API），支持 Polling 和 Webhook 两种模式。
Bot API 基地址: https://bot-go.apijia.cn/bot<TOKEN>/
支持双向交互：命令处理、多步对话、文本输入。
不支持 InlineKeyboard / CallbackQuery / 消息编辑，按钮降级为纯文本。
"""

import asyncio
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from src.notification.base import (
    BaseNotificationChannel, CommandResult,
    ChannelCapability, ChannelCapabilities,
)
from src._version import APP_VERSION

logger = logging.getLogger(__name__)
bot_raw_logger = logging.getLogger("bot_raw")

# 抑制 httpx 轮询产生的大量 INFO 日志，只保留 WARNING 及以上
logging.getLogger("httpx").setLevel(logging.WARNING)

# SC3 Bot API 基地址
SC3_BOT_API_BASE = "https://bot-go.apijia.cn/bot{token}"


class ServerChanChannel(BaseNotificationChannel):
    """Server酱³ Bot 通知渠道 — 支持双向交互"""

    channel_type = "serverchan3"
    display_name = "Server酱³"
    display_name_en = "ServerChan³"
    display_name_tw = "Server醬³"

    # SC3 Bot 渠道能力：仅支持富文本和链接，不支持按钮/回调/编辑
    _CAPABILITIES = ChannelCapabilities(
        capabilities={
            ChannelCapability.RICH_TEXT,
            ChannelCapability.LINKS,
        },
    )

    def __init__(self, channel_id: int, name: str, config: dict, notification_service):
        super().__init__(channel_id, name, config, notification_service)
        self._running = False
        self._polling_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._offset = 0  # getUpdates 偏移量
        self._client: Optional[httpx.AsyncClient] = None
        # 文字交互模式：编号 → callback_data 映射（per user）
        # {user_id: ["callback_data_1", "callback_data_2", ...]}
        # 用户输入数字 N → mapping[N-1]
        self._button_mappings: Dict[str, List[str]] = {}

    def get_capabilities(self) -> ChannelCapabilities:
        return self._CAPABILITIES

    # ─── 内部辅助 ───────────────────────────────

    def _api_url(self, method: str) -> str:
        """构造 Bot API 完整 URL（支持 VPS 出网代理路由 /out/）"""
        token = self.config.get("bot_token", "")
        api_proxy = self.config.get("sc3_api_proxy", "").strip().rstrip("/")
        if api_proxy:
            # 通用出网代理格式：{vps_url}/out/bot-go.apijia.cn/bot{token}/{method}
            return f"{api_proxy}/out/bot-go.apijia.cn/bot{token}/{method}"
        return f"{SC3_BOT_API_BASE.format(token=token)}/{method}"


    def _parse_id_list(self, key: str) -> set:
        raw = self.config.get(key, "")
        if not raw:
            return set()
        return {s.strip() for s in str(raw).split(",") if s.strip()}

    def _is_allowed(self, chat_id: int) -> bool:
        """权限检查：chat_id 即 SC3 uid"""
        uid = str(chat_id)
        admins = self._parse_id_list("admin_ids")
        allowed = self._parse_id_list("allowed_ids")
        if admins and uid in admins:
            return True
        if allowed:
            return uid in allowed
        return uid in admins if admins else True

    def _is_log_raw(self) -> bool:
        """检查是否启用原始日志"""
        return str(self.config.get("log_raw", "false")).lower() == "true"

    def _log_raw(self, direction: str, data):
        """记录原始交互日志"""
        if self._is_log_raw():
            bot_raw_logger.info(
                f"[SC3 Bot #{self.channel_id}] {direction}\n"
                f"{json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else data}\n"
                f"{'─' * 60}"
            )

    def _get_event_loop(self):
        """获取主事件循环"""
        if self._loop and self._loop.is_running():
            return self._loop
        self.logger.warning("主事件循环不可用")
        return None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 异步客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _relay_headers(self) -> dict:
        """当使用 VPS 出网代理时注入认证 Header"""
        if not self.config.get("sc3_api_proxy", "").strip():
            return {}
        key = self.config.get("__webhook_api_key", "")
        return {"X-Relay-Key": key} if key else {}

    # ─── 生命周期 ───────────────────────────────

    async def start(self):
        bot_token = self.config.get("bot_token", "")
        if not bot_token:
            self.logger.warning("Bot Token 未配置，跳过启动")
            return

        self._loop = asyncio.get_running_loop()
        self._running = True

        mode = self.config.get("mode", "polling")
        if mode == "webhook":
            self.logger.info("Server酱³ Bot 已启动（Webhook 模式）")
        else:
            self._start_polling()
            self.logger.info("Server酱³ Bot 已启动（轮询模式）")


    async def stop(self):
        self._running = False
        self._loop = None
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self.logger.info("Server酱³ Bot 已停止")

    # ─── 轮询 ─────────────────────────────────

    def _start_polling(self):
        """在后台线程中启动长轮询"""
        if self._polling_thread and self._polling_thread.is_alive():
            return

        def polling_worker():
            self.logger.info("Server酱³ 轮询已启动")
            # 读取用户配置的轮询超时（5-30秒）
            poll_timeout = self.config.get("polling_timeout", 25)
            try:
                poll_timeout = max(5, min(30, int(poll_timeout)))
            except (ValueError, TypeError):
                poll_timeout = 25
            http_timeout = poll_timeout + 10  # HTTP 超时比轮询超时多留余量
            while self._running:
                try:
                    url = self._api_url("getUpdates")
                    params = {"timeout": poll_timeout, "offset": self._offset}
                    # 轮询线程使用同步 httpx
                    with httpx.Client(timeout=http_timeout) as client:
                        while self._running:
                            try:
                                resp = client.get(url, params=params)
                                data = resp.json()
                                self._log_raw("⬇ getUpdates 响应", data)
                                if data.get("ok") and data.get("result"):
                                    for update in data["result"]:
                                        self._offset = update.get("update_id", 0) + 1
                                        params["offset"] = self._offset
                                        self._dispatch_update(update)
                            except httpx.ReadTimeout:
                                continue  # 长轮询超时是正常的
                            except Exception as e:
                                if self._running:
                                    self.logger.error(f"轮询请求异常: {e}")
                                    time.sleep(3)
                except Exception as e:
                    if self._running:
                        self.logger.error(f"轮询线程异常: {e}")
                        time.sleep(5)

        self._polling_thread = threading.Thread(
            target=polling_worker,
            name=f"sc3-poll-{self.channel_id}",
            daemon=True,
        )
        self._polling_thread.start()

    def _dispatch_update(self, update: dict):
        """将 update 分发到异步处理器"""
        self._log_raw("⬇ 收到 update", update)
        message = update.get("message")
        if not message:
            return
        # 兼容两种格式：
        # 文档格式（扁平）: {"chat_id": 1, "text": "..."}
        # TG兼容格式（嵌套）: {"chat": {"id": 1}, "text": "..."}
        chat_id = message.get("chat_id")
        if chat_id is None:
            chat_obj = message.get("chat")
            if isinstance(chat_obj, dict):
                chat_id = chat_obj.get("id")
        # 同样兼容 from.id 作为 fallback
        if chat_id is None:
            from_obj = message.get("from")
            if isinstance(from_obj, dict):
                chat_id = from_obj.get("id")
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            self.logger.debug(f"忽略无效消息: chat_id={chat_id}, text={text!r}")
            return
        self.logger.info(f"收到消息 [uid={chat_id}]: {text[:80]}")

        if not self._is_allowed(chat_id):
            # 无权限用户，静默忽略或发送提示
            loop = self._get_event_loop()
            if loop:
                asyncio.run_coroutine_threadsafe(
                    self._send_text(chat_id, "⛔ 你没有权限使用此机器人。"), loop
                )
            return

        loop = self._get_event_loop()
        if not loop:
            return

        # 判断是命令还是普通文本
        if text == "/":
            # 纯 "/" 输入 → 回复命令菜单
            asyncio.run_coroutine_threadsafe(
                self._send_menu(chat_id), loop
            )
        elif text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lstrip("/").split("@")[0]
            args = parts[1] if len(parts) > 1 else ""
            asyncio.run_coroutine_threadsafe(
                self._handle_command(cmd, chat_id, args), loop
            )
        else:
            asyncio.run_coroutine_threadsafe(
                self._handle_text(chat_id, text), loop
            )


    # ─── 命令 & 文本处理 ──────────────────────

    async def _handle_command(self, cmd: str, chat_id: int, args: str):
        """处理命令"""
        user_id = str(chat_id)
        if cmd == "cancel":
            result = await self.service.handle_cancel(user_id)
        else:
            result: CommandResult = await self.service.handle_command(
                cmd, user_id, args, self, chat_id=chat_id
            )
        await self._render_result(result, chat_id)

    async def _handle_text(self, chat_id: int, text: str):
        """处理普通文本（对话状态机 + 数字选择）"""
        user_id = str(chat_id)

        # 数字选择：检查是否有保存的按钮映射
        if text.isdigit():
            mapping = self._button_mappings.get(user_id)
            if mapping:
                idx = int(text) - 1  # 用户输入从1开始
                if 0 <= idx < len(mapping):
                    callback_data = mapping[idx]
                    self.logger.info(f"数字选择 [{text}] → 回调: {callback_data}")
                    # 清除映射，防止重复触发
                    self._button_mappings.pop(user_id, None)
                    result = await self.service.handle_callback(
                        callback_data, user_id, self, chat_id=chat_id, message_id=0
                    )
                    if result:
                        await self._render_result(result, chat_id)
                    return
                else:
                    await self._send_text(chat_id, f"⚠️ 无效编号，请输入 1-{len(mapping)} 之间的数字。")
                    return

        # 普通文本 → 对话状态机
        result: CommandResult = await self.service.handle_text_input(
            text, user_id, self, chat_id=chat_id
        )
        if result:
            await self._render_result(result, chat_id)

    # ─── 渲染引擎 ─────────────────────────────

    async def _render_result(self, result: CommandResult, chat_id: int):
        """渲染 CommandResult — SC3 不支持按钮/编辑/toast，全部降级为文本"""
        if not result:
            return
        text = result.text
        user_id = str(chat_id)

        # SC3 不支持 answer_callback_text（Telegram toast），降级为普通文本
        if not text and result.answer_callback_text:
            text = result.answer_callback_text

        if not text:
            return

        # 按钮降级为纯文本列表，并保存编号→callback_data 映射
        if result.reply_markup:
            from src.services.notification_service import NotificationService
            text = NotificationService._buttons_to_text_fallback(text, result.reply_markup)
            # 提取 callback_data 列表，顺序与 _buttons_to_text_fallback 编号一致
            mapping = []
            for row in result.reply_markup:
                for btn in row:
                    cb = btn.get("callback_data", "")
                    if cb:
                        mapping.append(cb)
            if mapping:
                self._button_mappings[user_id] = mapping
                text += f"\n\n💡 回复数字 1-{len(mapping)} 选择操作"
            else:
                self._button_mappings.pop(user_id, None)
        else:
            # 没有按钮时清除旧映射，避免误触
            self._button_mappings.pop(user_id, None)
        await self._send_text(chat_id, text)
        # 回写消息ID（虽然 SC3 不支持编辑，但保持接口一致）
        if result.next_state:
            self.service.update_conversation_message_id(user_id, 0)

    async def _send_text(self, chat_id: int, text: str, silent: bool = False):
        """通过 Bot API 发送文本消息"""
        try:
            client = await self._get_client()
            payload = {
                "chat_id": chat_id,
                "text": text,
                "silent": silent,
            }
            self._log_raw("⬆ sendMessage 请求", payload)
            resp = await client.post(
                self._api_url("sendMessage"),
                json=payload,
                headers=self._relay_headers(),
            )
            data = resp.json()
            self._log_raw("⬇ sendMessage 响应", data)
            if not data.get("ok", True):
                self.logger.error(f"发送消息失败: {data}")
        except Exception as e:
            self.logger.error(f"发送消息异常: {e}")

    # ─── 消息发送（系统通知） ──────────────────

    async def send_message(self, title: str, text: str, **kwargs):
        """通过 Bot API sendMessage 发送系统通知"""
        bot_token = self.config.get("bot_token", "")
        if not bot_token:
            return
        chat_id = kwargs.get("chat_id") or self.config.get("chat_id", "")
        if not chat_id:
            self.logger.warning("未配置 Chat ID，无法发送通知")
            return
        content = f"**{title}**\n{text}" if title else text
        try:
            await self._send_text(int(chat_id), content, silent=False)
        except Exception as e:
            self.logger.error(f"发送通知失败: {e}")

    # ─── 命令菜单 ─────────────────────────────

    async def _send_menu(self, chat_id: int):
        """用户输入 / 时回复可用命令列表"""
        menu_commands = self.service.get_menu_commands()
        if not menu_commands:
            return
        lines = ["📋 可用命令：", ""]
        for cmd, desc in menu_commands.items():
            lines.append(f"  {cmd} — {desc}")
        lines.append("")
        lines.append("输入命令即可使用，如 /help")
        await self._send_text(chat_id, "\n".join(lines))

    # ─── 连接测试 ───────────────────────────────

    async def test_connection(self) -> Dict[str, Any]:
        bot_token = self.config.get("bot_token", "")
        if not bot_token:
            return {"success": False, "message": "Bot Token 未配置"}
        try:
            relay_hdrs = self._relay_headers()
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self._api_url("getMe"), headers=relay_hdrs)
                data = resp.json()
                if data.get("ok"):
                    bot_info = data.get("result", {})
                    name = bot_info.get("first_name", "Bot")
                    # 发送测试消息到配置的 chat_id
                    chat_id = self.config.get("chat_id", "")
                    if chat_id:
                        try:
                            payload = {
                                "chat_id": int(chat_id),
                                "text": f"🔔 测试连接成功！\nBot: {name}\n来自 Misaka 弹幕服务器的测试消息。\n版本：v{APP_VERSION}",
                                "silent": False,
                            }
                            await client.post(self._api_url("sendMessage"), json=payload, headers=relay_hdrs)
                        except Exception as e:
                            self.logger.warning(f"测试消息发送失败: {e}")
                    return {
                        "success": True,
                        "message": f"连接成功！Bot: {name}" + (f"，测试消息已发送到 {chat_id}" if chat_id else ""),
                        "botInfo": bot_info,
                    }
                else:
                    return {"success": False, "message": f"连接失败: {data.get('description', '未知错误')}"}
        except Exception as e:
            return {"success": False, "message": f"连接异常: {e}"}

    # ─── Webhook ───────────────────────────────

    def process_webhook_update(self, update_json: dict) -> bool:
        """处理 Webhook 推送的 update"""
        if self.config.get("mode") != "webhook":
            return False
        # 验证 webhook secret（如果配置了）
        # 注意：secret 验证应在 API 层完成，这里仅处理消息
        self._dispatch_update(update_json)
        return True

    # ─── 配置 Schema ───────────────────────────

    @staticmethod
    def get_config_schema() -> list:
        return [
            {
                "key": "bot_token",
                "label": "Bot Token",
                "type": "password",
                "description": "从 Server酱³ App 的 Bot 管理页面获取的 Bot Token",
                "description_en": "Bot Token from Server酱³ App's Bot management page",
                "description_tw": "從 Server醬³ App 的 Bot 管理頁面取得的 Bot Token",
                "placeholder": "your-bot-token",
                "required": True,
            },
            {
                "key": "chat_id",
                "label": "Chat ID (uid)",
                "type": "string",
                "description": "默认消息接收者的 uid（即 Server酱³ 用户ID），用于接收系统通知",
                "description_en": "Default receiver uid (Server酱³ user ID) for system notifications",
                "description_tw": "預設訊息接收者的 uid（即 Server醬³ 使用者ID），用於接收系統通知",
                "placeholder": "1",
            },
            {
                "key": "admin_ids",
                "label": "管理员用户ID",
                "label_en": "Admin User IDs",
                "label_tw": "管理員使用者ID",
                "type": "string",
                "description": "拥有管理权限的用户 uid，多个用逗号分隔",
                "description_en": "User UIDs with admin privileges, separated by commas",
                "description_tw": "擁有管理權限的使用者 uid，多個用逗號分隔",
                "placeholder": "1,2",
            },
            {
                "key": "allowed_ids",
                "label": "允许的用户ID",
                "label_en": "Allowed User IDs",
                "label_tw": "允許的使用者ID",
                "type": "string",
                "description": "允许使用 Bot 交互的用户 uid，多个用逗号分隔。留空则仅管理员可用",
                "description_en": "User UIDs allowed to interact with the Bot, separated by commas. Leave empty for admin-only",
                "description_tw": "允許使用 Bot 互動的使用者 uid，多個用逗號分隔。留空則僅管理員可用",
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
                "key": "polling_timeout",
                "label": "轮询超时时间",
                "label_en": "Polling Timeout",
                "label_tw": "輪詢逾時時間",
                "type": "slider",
                "description": "长轮询等待时间（秒），值越大越省流量但响应稍慢",
                "description_en": "Long polling wait time (seconds). Higher values save bandwidth but response is slightly slower.",
                "description_tw": "長輪詢等待時間（秒），值越大越省流量但回應稍慢",
                "min": 5,
                "max": 30,
                "step": 1,
                "default": 25,
                "suffix": "秒",
                "suffix_en": "sec",
                "suffix_tw": "秒",
                "marks": {5: "5s", 15: "15s", 30: "30s"},
                "visibleWhen": {"mode": "polling"},
            },
            {
                "key": "webhook_base_url",
                "label": "外部访问地址",
                "label_en": "External Access URL",
                "label_tw": "外部存取位址",
                "type": "string",
                "description": "你的服务器公网地址（如 https://my-domain.com），需在 SC3 App 的 Bot 管理中配置此 Webhook 地址",
                "description_en": "Your server's public URL (e.g. https://my-domain.com). Configure this Webhook URL in the SC3 App's Bot management.",
                "description_tw": "你的伺服器公網位址（如 https://my-domain.com），需在 SC3 App 的 Bot 管理中配置此 Webhook 位址",
                "placeholder": "https://your-domain.com",
                "visibleWhen": {"mode": "webhook"},
            },
            {
                "key": "tunnel_enabled",
                "label": "启用 VPS 隧道连接",
                "label_en": "Enable VPS Tunnel",
                "label_tw": "啟用 VPS 隧道連接",
                "type": "boolean",
                "description": "启用后，弹幕库将通过上方「外部访问地址」建立 WebSocket 反向隧道，将 SC3 回调转发到本地（无需公网 IP）",
                "description_en": "When enabled, a WebSocket reverse tunnel is established via the external URL to forward SC3 callbacks locally (no public IP needed).",
                "description_tw": "啟用後，彈幕庫將透過上方「外部存取位址」建立 WebSocket 反向隧道，將 SC3 回呼轉發到本地（無需公網 IP）",
                "default": False,
                "visibleWhen": {"mode": "webhook"},
            },
            {
                "key": "webhook_secret",
                "label": "Webhook Secret",
                "type": "password",
                "description": "Webhook 密钥（可选），配置后会验证请求头中的 X-Sc3Bot-Webhook-Secret",
                "description_en": "Webhook secret (optional). When set, verifies X-Sc3Bot-Webhook-Secret header.",
                "description_tw": "Webhook 金鑰（可選），配置後會驗證請求標頭中的 X-Sc3Bot-Webhook-Secret",
                "placeholder": "",
                "visibleWhen": {"mode": "webhook"},
            },
            {
                "key": "sc3_api_proxy",
                "label": "API 出网代理地址",
                "label_en": "API Outbound Proxy",
                "label_tw": "API 出網代理位址",
                "type": "string",
                "description": "填入 VPS 地址（如 http://vps.example.com），Bot 的 API 请求将通过 VPS 出网。留空则直连 bot-go.apijia.cn",
                "description_en": "Enter VPS address (e.g. http://vps.example.com). Bot API requests will go through VPS. Leave empty to connect directly to bot-go.apijia.cn.",
                "description_tw": "填入 VPS 位址（如 http://vps.example.com），Bot 的 API 請求將透過 VPS 出網。留空則直連 bot-go.apijia.cn",
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
        ]