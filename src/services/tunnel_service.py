"""
TunnelService — 弹幕库侧 WebSocket 反向隧道客户端

使用 aiohttp（已在 requirements.txt 中），无需额外依赖。

工作流程：
  1. 连接 VPS 控制 WebSocket:  ws://VPS/ws/ctrl/{key}
  2. 收到 new_conn 消息（含完整 HTTP 请求信息）后：
     - 建立数据 WebSocket:  ws://VPS/ws/data/{key}/{id}
     - 用 aiohttp 向本地 :7768 发起 HTTP 请求
     - 把响应序列化为 JSON 通过数据 WS 发回给 relay
"""
import asyncio
import json
import logging
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

_CONNECT_RETRY_DELAYS = [5, 10, 20, 40, 60, 120, 300]  # 秒，指数退避


class TunnelService:
    """WebSocket 反向隧道客户端服务"""

    def __init__(self) -> None:
        self._enabled: bool = False
        self._vps_ws_url: str = ""   # ws://VPS_HOST/ws/ (不含 ctrl/data 后缀)
        self._webhook_key: str = ""
        self._local_port: int = 7768

        self._task: Optional[asyncio.Task] = None
        self._status: str = "disabled"

    # ──────────────────────────────────────────────────────────
    # 公开 API
    # ──────────────────────────────────────────────────────────

    def configure(
        self,
        enabled: bool,
        vps_proxy_url: str,
        webhook_key: str,
        local_port: int = 7768,
    ) -> bool:
        """
        更新配置。返回 True 表示配置有变化，需要调用 apply()。
        vps_proxy_url 示例: "http://1.2.3.4" 或 "https://relay.example.com"
        """
        ws_url = self._to_ws_base(vps_proxy_url)
        changed = (
            self._enabled != enabled
            or self._vps_ws_url != ws_url
            or self._webhook_key != webhook_key
            or self._local_port != local_port
        )
        self._enabled = enabled
        self._vps_ws_url = ws_url
        self._webhook_key = webhook_key
        self._local_port = local_port
        return changed

    async def apply(self) -> None:
        """停止旧隧道并按当前配置启动新隧道"""
        await self._stop_task()
        if self._enabled and self._vps_ws_url and self._webhook_key:
            self._task = asyncio.create_task(self._ctrl_loop())
            log.info("[Tunnel] 隧道服务已启动，目标: %s", self._vps_ws_url)
        else:
            self._status = "disabled"
            log.info("[Tunnel] 隧道已禁用")

    async def stop(self) -> None:
        await self._stop_task()
        self._status = "disabled"
        log.info("[Tunnel] 隧道服务已停止")

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def status(self) -> str:
        return self._status

    # ──────────────────────────────────────────────────────────
    # 内部：控制循环（带自动重连）
    # ──────────────────────────────────────────────────────────

    async def _ctrl_loop(self) -> None:
        retry_idx = 0
        ctrl_url = f"{self._vps_ws_url}ctrl/{self._webhook_key}"

        while True:
            self._status = "connecting"
            log.info("[Tunnel] 连接控制 WebSocket: %s", ctrl_url)
            try:
                timeout = aiohttp.ClientTimeout(total=None, connect=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(
                        ctrl_url,
                        heartbeat=30,
                        receive_timeout=None,
                    ) as ws:
                        self._status = "connected"
                        retry_idx = 0
                        log.info("[Tunnel] 控制连接已建立，等待新连接通知...")
                        await self._handle_ctrl(ws)

            except asyncio.CancelledError:
                return
            except Exception as e:
                self._status = "reconnecting"
                delay = _CONNECT_RETRY_DELAYS[min(retry_idx, len(_CONNECT_RETRY_DELAYS) - 1)]
                retry_idx += 1
                log.warning("[Tunnel] 连接断开: %s — %d 秒后重试", e, delay)
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return

    async def _handle_ctrl(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """处理控制 WebSocket 消息"""
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "new_conn":
                    conn_id = data.get("id", "")
                    if conn_id:
                        asyncio.create_task(self._handle_data_conn(data))
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break

    # ──────────────────────────────────────────────────────────
    # 内部：数据连接（每个回调请求一个，HTTP over WebSocket）
    # ──────────────────────────────────────────────────────────

    async def _handle_data_conn(self, req_info: dict) -> None:
        """
        处理一次回调请求：
          1. 建立数据 WS（让 relay 知道通道就绪）
          2. 用 aiohttp 向本地发起 HTTP 请求
          3. 把 HTTP 响应序列化后通过数据 WS 发回
        """
        conn_id: str = req_info.get("id", "")
        data_url = f"{self._vps_ws_url}data/{self._webhook_key}/{conn_id}"
        method: str = req_info.get("method", "GET")
        path: str = req_info.get("path", "/")
        headers: dict = req_info.get("headers", {})
        body_hex: str = req_info.get("body", "")
        body_bytes = bytes.fromhex(body_hex) if body_hex else b""

        log.debug("[Tunnel] [%s] %s %s — 建立数据 WS", conn_id[:8], method, path)

        try:
            timeout = aiohttp.ClientTimeout(total=None, connect=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(data_url) as ws:
                    log.debug("[Tunnel] [%s] 数据 WS 就绪，转发至本地 %d",
                              conn_id[:8], self._local_port)
                    await self._forward_http(
                        ws, session, method, path, headers, body_bytes, conn_id
                    )
        except Exception as e:
            log.warning("[Tunnel] [%s] 数据连接异常: %s", conn_id[:8], e)

    async def _forward_http(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        session: aiohttp.ClientSession,
        method: str,
        path: str,
        req_headers: dict,
        body: bytes,
        conn_id: str,
    ) -> None:
        """向本地发 HTTP 请求，把响应序列化后发回数据 WS"""
        local_url = f"http://127.0.0.1:{self._local_port}{path}"

        # 过滤掉不适合转发的 hop-by-hop 及 host 类 header
        # content-length 必须过滤：aiohttp 发 data=body 时会自动计算，不能重复设置
        skip = {"host", "connection", "transfer-encoding", "content-encoding",
                "content-length", "upgrade", "te", "trailers", "proxy-connection"}
        forward_headers = {k: v for k, v in req_headers.items() if k.lower() not in skip}
        # 强制 Host 指向本地
        forward_headers["Host"] = f"127.0.0.1:{self._local_port}"

        try:
            req_timeout = aiohttp.ClientTimeout(total=30, connect=5)
            async with session.request(
                method, local_url,
                headers=forward_headers,
                data=body,
                timeout=req_timeout,
                allow_redirects=False,
            ) as resp:
                resp_body = await resp.read()
                resp_headers = dict(resp.headers)

                resp_info = {
                    "status": resp.status,
                    "headers": resp_headers,
                    "body": resp_body.hex(),
                }
                await ws.send_str(json.dumps(resp_info))
                log.debug("[Tunnel] [%s] 本地响应 %d，%d B",
                          conn_id[:8], resp.status, len(resp_body))
        except Exception as e:
            log.error("[Tunnel] [%s] 本地请求失败: %s", conn_id[:8], e)
            err_resp = {"status": 502, "headers": {}, "body": b"".hex()}
            await ws.send_str(json.dumps(err_resp))

    # ──────────────────────────────────────────────────────────
    # 内部：工具方法
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _to_ws_base(url: str) -> str:
        """将 http(s):// URL 转换为 ws(s)://... 且确保末尾为 /ws/"""
        url = url.rstrip("/")
        if url.startswith("https://"):
            url = "wss://" + url[8:]
        elif url.startswith("http://"):
            url = "ws://" + url[7:]
        return url + "/ws/"

    async def _stop_task(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
