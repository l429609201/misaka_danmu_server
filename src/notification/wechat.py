"""
企业微信应用消息通知渠道实现（双向交互）
- 推送：企业微信应用消息 API
- 接收：Webhook 回调（GET 验证 + POST 接收 XML 消息）
- 参考：MoviePilot 项目企业微信模块 & 腾讯官方 WXBizMsgCrypt3
"""

import asyncio
import base64
import hashlib
import logging
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

import httpx

try:
    from Crypto.Cipher import AES
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

from src.notification.base import (
    BaseNotificationChannel,
    ChannelCapability, ChannelCapabilities, CommandResult,
)
from src._version import APP_VERSION

logger = logging.getLogger(__name__)
bot_raw_logger = logging.getLogger("bot_raw")
WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
WECOM_DEFAULT_HOST = "https://qyapi.weixin.qq.com"


# ══════════════════════════════════════════════════
# 企业微信消息加解密（内联自腾讯官方 WXBizMsgCrypt3）
# ══════════════════════════════════════════════════

def _pkcs7_decode(data: bytes) -> bytes:
    pad = data[-1]
    if pad < 1 or pad > 32:
        pad = 0
    return data[:-pad]


def _sha1_sign(*args) -> str:
    return hashlib.sha1("".join(sorted(args)).encode("utf-8")).hexdigest()


class _WXCrypt:
    """企业微信 AES-256-CBC 消息加解密"""

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        if not _HAS_CRYPTO:
            raise RuntimeError("请安装 pycryptodome: pip install pycryptodome")
        try:
            self.key = base64.b64decode(encoding_aes_key + "=")
            assert len(self.key) == 32
        except Exception:
            raise ValueError("EncodingAESKey 非法，必须是43位字符串")
        self.token = token
        self.corp_id = corp_id

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str):
        """URL 验证，返回 (ok: bool, plain_echostr: str|None)"""
        if _sha1_sign(self.token, timestamp, nonce, echostr) != msg_signature:
            return False, None
        return self._decrypt(echostr)

    def decrypt_msg(self, msg_signature: str, timestamp: str, nonce: str, post_data: str):
        """解密消息，返回 (ok: bool, xml_content: str|None)"""
        try:
            encrypt = ET.fromstring(post_data).findtext("Encrypt") or ""
        except ET.ParseError:
            return False, None
        if _sha1_sign(self.token, timestamp, nonce, encrypt) != msg_signature:
            return False, None
        return self._decrypt(encrypt)

    def _decrypt(self, text: str):
        try:
            cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
            plain_b = _pkcs7_decode(cipher.decrypt(base64.b64decode(text)))
            content = plain_b[16:]
            msg_len = struct.unpack(">I", content[:4])[0]
            xml_content = content[4:4 + msg_len].decode("utf-8")
            from_corp_id = content[4 + msg_len:].decode("utf-8")
            if from_corp_id != self.corp_id:
                return False, None
            return True, xml_content
        except Exception:
            return False, None


# ══════════════════════════════════════════════════
# 通知渠道实现
# ══════════════════════════════════════════════════

class WeChatChannel(BaseNotificationChannel):
    """企业微信应用消息通知渠道（推送 + 双向交互）"""

    channel_type = "wechat"
    display_name = "企业微信"
    hide_proxy = True   # 企业微信使用 wecom_proxy 反代地址，不需要全局 HTTP 代理开关

    _CAPABILITIES = ChannelCapabilities(
        capabilities={
            ChannelCapability.RICH_TEXT,
            ChannelCapability.LINKS,
            ChannelCapability.MENU_COMMANDS,
        },
    )

    def __init__(self, channel_id, name, config, notification_service):
        super().__init__(channel_id, name, config, notification_service)
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._crypt: Optional[_WXCrypt] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # 按钮编号→callback_data 映射（用于数字选择降级交互）
        self._button_mappings: Dict[str, list] = {}

    def get_capabilities(self) -> ChannelCapabilities:
        return self._CAPABILITIES

    def _api_base(self) -> str:
        """返回企业微信 API Base URL，智能识别代理格式：
        - 留空 → 直连官方 https://qyapi.weixin.qq.com/cgi-bin
        - 包含 /cgi-bin → 用户给了完整路径，直接用
        - 以 /out 结尾 → MoviePilot 通用出网代理格式
        - 其他 → 简单反代（Flask/Nginx 直接转发 /cgi-bin/*）
        """
        proxy = self.config.get("wecom_proxy", "").strip().rstrip("/")
        if not proxy:
            return WECOM_API_BASE

        # 已包含 /cgi-bin → 完整路径，直接用
        if "/cgi-bin" in proxy:
            return proxy if proxy.endswith("/cgi-bin") else proxy.split("/cgi-bin")[0] + "/cgi-bin"

        # 以 /out 结尾 → MoviePilot 通用出网代理格式
        if proxy.endswith("/out"):
            return f"{proxy}/qyapi.weixin.qq.com/cgi-bin"

        # 默认：简单反代，直接拼 /cgi-bin
        # 兼容 Flask/Nginx 等直接转发 /cgi-bin/* 到企业微信的代理
        return f"{proxy}/cgi-bin"

    def _relay_headers(self) -> dict:
        """当使用 VPS 代理时注入认证 Header，防止代理被滥用"""
        proxy = self.config.get("wecom_proxy", "").strip()
        if not proxy:
            return {}
        key = self.config.get("__webhook_api_key", "")
        return {"X-Relay-Key": key} if key else {}

    def _wecom_proxy(self) -> Optional[str]:
        """企业微信不使用 httpx 代理，API 请求走 _api_base() 的反代地址"""
        return None

    def _is_log_raw(self) -> bool:
        return str(self.config.get("log_raw", "false")).lower() == "true"

    def _log_raw(self, direction: str, data):
        if self._is_log_raw():
            import json as _json
            bot_raw_logger.info(
                f"[WeCom #{self.channel_id}] {direction}\n"
                f"{_json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else data}\n"
                f"{'─' * 60}"
            )

    def _get_crypt(self) -> Optional[_WXCrypt]:
        """懒加载加解密实例"""
        if self._crypt is not None:
            return self._crypt
        t = self.config.get("msg_token", "").strip()
        k = self.config.get("encoding_aes_key", "").strip()
        c = self.config.get("corp_id", "").strip()
        if not (t and k and c):
            return None
        try:
            self._crypt = _WXCrypt(t, k, c)
        except Exception as e:
            self.logger.error(f"加解密初始化失败: {e}")
        return self._crypt

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self.logger.info("企业微信渠道已就绪（推送 + Webhook 双向交互）")
        cmds = self.service.get_menu_commands()
        if cmds:
            self.register_commands(cmds)

    async def stop(self):
        self._access_token = None
        self._crypt = None
        self._loop = None
        self.logger.info("企业微信渠道已停止")

    async def _get_access_token(self) -> Optional[str]:
        now = time.time()
        if self._access_token and now < self._token_expires_at - 300:
            return self._access_token
        corp_id = self.config.get("corp_id", "").strip()
        corp_secret = self.config.get("corp_secret", "").strip()
        if not corp_id or not corp_secret:
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._api_base()}/gettoken",
                    params={"corpid": corp_id, "corpsecret": corp_secret},
                    headers=self._relay_headers(),
                )
                d = resp.json()
                if d.get("errcode", -1) == 0:
                    self._access_token = d["access_token"]
                    self._token_expires_at = now + d.get("expires_in", 7200)
                    return self._access_token
                self.logger.error(f"获取 token 失败: {d.get('errmsg')} (code={d.get('errcode')})")
        except Exception as e:
            self.logger.error(f"获取 token 异常: {e}")
        return None

    async def _api_post(self, path: str, payload: dict, extra_params: Optional[dict] = None) -> Optional[dict]:
        """统一 API POST 请求"""
        token = await self._get_access_token()
        if not token:
            return None
        params = {"access_token": token}
        if extra_params:
            params.update(extra_params)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._api_base()}/{path}",
                    params=params,
                    json=payload,
                    headers=self._relay_headers(),
                )
                return resp.json()
        except Exception as e:
            self.logger.error(f"API [{path}] 异常: {e}")
            return None

    async def send_message(self, title: str, text: str, **kwargs):
        agent_id = self.config.get("agent_id", "").strip()
        if not agent_id:
            return
        to_user = kwargs.get("to_user") or self.config.get("to_user", "@all").strip() or "@all"
        image: str = kwargs.get("image", "")   # 封面图 URL
        url: str = kwargs.get("url", "")       # 点击跳转 URL

        if image or url:
            # 图文消息（news 类型），参考 MoviePilot 实现
            article = {
                "title": title or text[:64],
                "description": text,
                "picurl": image,
                "url": url,
            }
            payload = {
                "touser": to_user,
                "msgtype": "news",
                "agentid": int(agent_id),
                "news": {"articles": [article]},
            }
        else:
            content = f"【{title}】\n{text}" if title else text
            payload = {
                "touser": to_user,
                "msgtype": "text",
                "agentid": int(agent_id),
                "text": {"content": content},
            }
        d = await self._api_post("message/send", payload)
        if d and d.get("errcode", -1) != 0:
            self.logger.error(f"发送消息失败: {d.get('errmsg')}")

    async def _send_text_to(self, to_user: str, text: str):
        """回复消息给指定用户"""
        agent_id = self.config.get("agent_id", "").strip()
        if not agent_id:
            return
        payload = {"touser": to_user, "msgtype": "text", "agentid": int(agent_id), "text": {"content": text}}
        self._log_raw("⬆ sendMessage 请求", payload)
        d = await self._api_post("message/send", payload)
        self._log_raw("⬇ sendMessage 响应", d or {})
        if d and d.get("errcode", -1) != 0:
            self.logger.error(f"回复失败: {d.get('errmsg')}")

    def register_commands(self, commands: Dict[str, str]) -> None:
        """注册企业微信自定义菜单（click 按钮，key=命令名）"""
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._register_menu_async(commands), loop)

    async def _register_menu_async(self, commands: Dict[str, str]):
        agent_id = self.config.get("agent_id", "").strip()
        if not agent_id:
            return
        # 每组最多5个子按钮，一级菜单最多3组
        buttons = []
        items = list(commands.items())
        for i in range(0, min(len(items), 15), 5):
            chunk = items[i:i + 5]
            if len(chunk) == 1:
                cmd, desc = chunk[0]
                buttons.append({"type": "click", "name": desc[:8], "key": cmd})
            else:
                buttons.append({
                    "name": f"功能{i // 5 + 1}",
                    "sub_button": [{"type": "click", "name": d[:8], "key": c} for c, d in chunk],
                })
        if not buttons:
            return
        d = await self._api_post("menu/create", {"button": buttons[:3]}, extra_params={"agentid": agent_id})
        if d is None:
            return
        if d.get("errcode", -1) == 0:
            self.logger.info("企业微信菜单注册成功")
        else:
            self.logger.error(f"菜单注册失败: {d.get('errmsg')}")

    def process_webhook_update(self, update_data: dict) -> Any:
        """
        处理企业微信 Webhook 回调（由通用 Webhook 路由调用）
        update_data 格式：
          GET:  {"method": "GET",  "params": {msg_signature, timestamp, nonce, echostr}}
          POST: {"method": "POST", "params": {msg_signature, timestamp, nonce}, "body": "XML"}
        返回：
          GET 成功 → 明文 echostr 字符串（路由层用 PlainTextResponse 返回给企业微信）
          POST 成功 → True
          失败 → False
        """
        method = update_data.get("method", "POST")
        params = update_data.get("params", {})

        if method == "GET":
            return self._handle_verify(params)

        body = update_data.get("body", "")
        if not body:
            return False

        crypt = self._get_crypt()
        if crypt:
            ok, xml_content = crypt.decrypt_msg(
                params.get("msg_signature", ""),
                params.get("timestamp", ""),
                params.get("nonce", ""),
                body,
            )
            if not ok:
                self.logger.error("消息解密失败，请检查 Token/EncodingAESKey 配置")
                return False
        else:
            # 未配置加解密时，尝试直接解析明文 XML
            xml_content = body

        self._dispatch_xml(xml_content)
        return True

    def _handle_verify(self, params: dict):
        """处理 GET URL 验证，成功返回明文 echostr，失败返回 False"""
        crypt = self._get_crypt()
        if not crypt:
            return params.get("echostr") or False
        ok, plain = crypt.verify_url(
            params.get("msg_signature", ""),
            params.get("timestamp", ""),
            params.get("nonce", ""),
            params.get("echostr", ""),
        )
        return plain if ok else False

    def _dispatch_xml(self, xml_content: str):
        """解析企业微信推送的 XML，分发到对应处理器"""
        self._log_raw("⬇ 收到 XML 消息", xml_content)
        try:
            tree = ET.fromstring(xml_content)
            msg_type = (tree.findtext("MsgType") or "").lower()
            from_user = tree.findtext("FromUserName") or ""

            if msg_type == "event":
                event = (tree.findtext("Event") or "").lower()
                if event == "click":
                    # 用户点击菜单按钮，EventKey 即命令名
                    key = (tree.findtext("EventKey") or "").lstrip("/")
                    self._schedule(self._handle_command(key, from_user, ""))
                elif event in ("subscribe", "enter_agent"):
                    self._schedule(self._handle_command("start", from_user, ""))
            elif msg_type == "text":
                text = (tree.findtext("Content") or "").strip()
                if text.startswith("/"):
                    parts = text.split(maxsplit=1)
                    cmd = parts[0].lstrip("/")
                    args = parts[1] if len(parts) > 1 else ""
                    self._schedule(self._handle_command(cmd, from_user, args))
                else:
                    self._schedule(self._handle_text(from_user, text))
        except ET.ParseError as e:
            self.logger.error(f"XML 解析失败: {e}")

    def _schedule(self, coro):
        """在主事件循环中调度异步任务"""
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)

    async def _handle_command(self, cmd: str, user_id: str, args: str):
        if cmd == "cancel":
            result = await self.service.handle_cancel(user_id)
        else:
            result: CommandResult = await self.service.handle_command(
                cmd, user_id, args, self, chat_id=user_id
            )
        if result and result.text:
            await self._render_result(user_id, result)

    async def _handle_text(self, user_id: str, text: str):
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
                        callback_data, user_id, self, chat_id=user_id, message_id=0
                    )
                    if result and result.text:
                        await self._render_result(user_id, result)
                    return
                else:
                    await self._send_text_to(user_id, f"⚠️ 无效编号，请输入 1-{len(mapping)} 之间的数字。")
                    return

        # 普通文本 → 对话状态机
        result: CommandResult = await self.service.handle_text_input(
            text, user_id, self, chat_id=user_id
        )
        if result and result.text:
            await self._render_result(user_id, result)

    async def _send_news_to(self, to_user: str, articles: list) -> None:
        """发送图文消息（news 类型），articles 最多 8 条"""
        agent_id = self.config.get("agent_id", "").strip()
        if not agent_id or not articles:
            return
        payload = {
            "touser": to_user,
            "msgtype": "news",
            "agentid": int(agent_id),
            "news": {"articles": articles[:8]},
        }
        self._log_raw("⬆ sendNews 请求", payload)
        d = await self._api_post("message/send", payload)
        self._log_raw("⬇ sendNews 响应", d or {})
        if d and d.get("errcode", -1) != 0:
            self.logger.error(f"发送图文消息失败: {d.get('errmsg')}")

    async def _render_result(self, user_id: str, result: CommandResult):
        """渲染响应 — 企业微信无内联按钮，降级为编号文本列表"""
        if not result:
            return
        text = result.text

        # answer_callback_text 降级为普通文本
        if not text and result.answer_callback_text:
            text = result.answer_callback_text

        if not text:
            return

        # 按钮降级为纯文本列表，并保存编号→callback_data 映射
        if result.reply_markup:
            from src.services.notification_service import NotificationService
            text = NotificationService._buttons_to_text_fallback(text, result.reply_markup)
            # 提取 callback_data 列表，顺序与编号一致
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

        # 有图文 articles 时先发 news 卡片（仅有 picurl 的条目才有意义）
        # 同时把文字正文只保留第一行（标题行），去掉重复列表，只保留操作提示
        if result.articles:
            has_image = any(a.get("picurl") for a in result.articles)
            if has_image:
                await self._send_news_to(user_id, result.articles)
                # 文字部分去掉结果列表，只保留第一行标题 + 操作选项
                first_line = text.split("\n")[0]
                ops_start = text.find("可用操作：")
                if ops_start != -1:
                    ops_text = text[ops_start:]
                    hint_start = text.rfind("\n\n💡")
                    hint = text[hint_start:] if hint_start != -1 else ""
                    text = first_line + "\n\n" + ops_text + (hint if hint not in ops_text else "")
                else:
                    text = first_line + "\n\n" + text[text.find("💡"):]  if "💡" in text else first_line

        await self._send_text_to(user_id, text)

    async def test_connection(self) -> Dict[str, Any]:
        if not all(self.config.get(k, "").strip() for k in ("corp_id", "corp_secret", "agent_id")):
            return {"success": False, "message": "请填写 corp_id、corp_secret 和 agent_id"}
        self._access_token = None
        token = await self._get_access_token()
        if not token:
            return {"success": False, "message": "获取 access_token 失败，请检查 corp_id 和 corp_secret"}
        try:
            await self.send_message("测试连接", f"✅ Misaka 弹幕服务器 - 企业微信渠道连接测试成功！\n版本：v{APP_VERSION}")
            return {"success": True, "message": "连接成功！测试消息已发送"}
        except Exception as e:
            return {"success": False, "message": f"测试消息发送失败: {e}"}

    @staticmethod
    def get_config_schema() -> list:
        return [
            {
                "key": "corp_id",
                "label": "企业 CorpID",
                "type": "string",
                "description": "企业微信管理后台 → 我的企业 → 企业信息 → 企业ID",
                "placeholder": "ww1234567890abcdef",
                "required": True,
            },
            {
                "key": "corp_secret",
                "label": "应用 Secret",
                "type": "password",
                "description": "企业微信管理后台 → 应用管理 → 自建应用 → 详情 → Secret",
                "placeholder": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "required": True,
            },
            {
                "key": "agent_id",
                "label": "应用 AgentID",
                "type": "string",
                "description": "企业微信管理后台 → 应用管理 → 自建应用 → 详情 → AgentId",
                "placeholder": "1000001",
                "required": True,
            },
            {
                "key": "to_user",
                "label": "接收者",
                "type": "string",
                "description": "接收消息的用户ID，多个用 | 分隔。@all 表示全体成员",
                "placeholder": "@all",
            },
            {
                "key": "msg_token",
                "label": "消息 Token",
                "type": "string",
                "description": "企业微信后台 → 应用 → 接收消息 → Token（启用双向交互时必填）",
                "placeholder": "随机字符串",
            },
            {
                "key": "encoding_aes_key",
                "label": "消息 EncodingAESKey",
                "type": "password",
                "description": "企业微信后台 → 应用 → 接收消息 → EncodingAESKey（43位，启用双向交互时必填）",
                "placeholder": "43位随机字符串",
            },
            {
                "key": "wecom_proxy",
                "label": "API 反向代理地址",
                "type": "string",
                "description": "企业微信 API 反向代理地址。支持多种格式：① 简单反代（如 http://192.168.1.5:9034，自动拼接 /cgi-bin）② MP 通用出网代理（如 https://vps.com/out）③ 完整路径（如 http://host:port/cgi-bin）。留空则直连官方地址。",
                "placeholder": "http://192.168.1.5:9034",
            },
            {
                "key": "tunnel_enabled",
                "label": "启用 VPS 隧道连接",
                "type": "boolean",
                "description": "启用后，弹幕库将使用上方「API 反向代理地址」作为 VPS 目标，通过 wstunnel 建立反向隧道，使 VPS 收到的回调自动转发到本地弹幕库。认证密钥使用系统设置 → Webhook API Key。",
                "default": False,
            },
            {
                "key": "server_url",
                "label": "外网访问地址",
                "type": "string",
                "description": "服务器外网地址，用于生成企业微信回调 URL（如 https://example.com）",
                "placeholder": "https://example.com",
            },
            {
                "key": "log_raw",
                "label": "记录原始交互",
                "type": "boolean",
                "description": "启用后，Bot 的所有收发消息将记录到 config/logs/bot_raw.log 文件中，用于调试",
                "default": False,
            },
        ]
