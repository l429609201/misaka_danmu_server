"""
御坂助手 · MCP 传输层
------------------------------------------------------------
三种传输协议的会话实现，统一 notify/request 接口：
  - stdio：拉起子进程，按行收发 JSON-RPC
  - streamable_http：单次 POST，响应可为 JSON 或 SSE
  - sse：先建 SSE 长连接拿 endpoint，再 POST 消息、从流上读响应

所有会话均为异步上下文管理器，退出时清理资源。
依赖导入统一置于文件头部，避免循环导入。
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import httpx

from .protocol import (
    build_message,
    extract_result,
    iter_sse_events,
    load_sse_payload,
    new_request_id,
    normalize_timeout,
    parse_sse_text,
)
from .schemas import McpServerConfig

logger = logging.getLogger(__name__)

# 子进程终止的宽限时间（秒），超时则强杀
_TERMINATE_GRACE = 2.0


def build_subprocess_env(extra_env: Optional[Dict[str, str]]) -> Dict[str, str]:
    """构造子进程环境变量：继承当前环境 + 配置追加项。"""
    env = dict(os.environ)
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    # 强制子进程 UTF-8 输出，避免 Windows 下中文乱码
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


class StdioSession:
    """stdio 传输：按操作生命周期拉起子进程。"""

    def __init__(self, server: McpServerConfig) -> None:
        self.server = server
        self.process: Optional[asyncio.subprocess.Process] = None
        self._stderr_task: Optional[asyncio.Task] = None

    async def __aenter__(self) -> "StdioSession":
        if not self.server.command:
            raise RuntimeError("stdio 传输缺少启动命令（command）")
        self.process = await asyncio.create_subprocess_exec(
            self.server.command,
            *(self.server.args or []),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=build_subprocess_env(self.server.env),
        )
        # 持续抽干 stderr，否则管道满了会阻塞子进程
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        task = self._stderr_task
        self._stderr_task = None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if not self.process or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=_TERMINATE_GRACE)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()

    async def _drain_stderr(self) -> None:
        """读取子进程 stderr 并转为 debug 日志。"""
        if not self.process or not self.process.stderr:
            return
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                logger.debug(f"MCP stdio[{self.server.name}] stderr: {text}")
        except asyncio.CancelledError:
            return

    async def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        await self._write(build_message(method, params))

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        request_id = new_request_id()
        await self._write(build_message(method, params, request_id=request_id))
        # 循环读取直到拿到 ID 匹配的响应（跳过服务端主动推送的其他消息）
        while True:
            payload = await self._read()
            if payload.get("id") == request_id:
                return extract_result(payload, request_id)

    async def _write(self, payload: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("stdio 子进程未启动")
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.process.stdin.write(data.encode("utf-8"))
        await self.process.stdin.drain()

    async def _read(self) -> Dict[str, Any]:
        if not self.process or not self.process.stdout:
            raise RuntimeError("stdio 子进程未启动")
        timeout = normalize_timeout(self.server.timeout)
        while True:
            line = await asyncio.wait_for(
                self.process.stdout.readline(), timeout=timeout
            )
            if not line:
                raise RuntimeError("stdio 子进程已退出")
            try:
                payload = json.loads(line.decode("utf-8"))
            except ValueError:
                # 子进程可能打印非 JSON 日志，忽略继续读
                logger.debug(f"忽略非 JSON 的 MCP stdout 行：{line!r}")
                continue
            if isinstance(payload, dict):
                return payload



class StreamableHttpSession:
    """streamable_http 传输：单次 POST，响应可为 JSON 或 SSE。"""

    def __init__(self, server: McpServerConfig) -> None:
        self.server = server
        self._client: Optional[httpx.AsyncClient] = None
        # 服务端下发的会话 ID，后续请求需回传
        self._session_id: Optional[str] = None

    async def __aenter__(self) -> "StreamableHttpSession":
        if not self.server.url:
            raise RuntimeError("streamable_http 传输缺少服务地址（url）")
        timeout = normalize_timeout(self.server.timeout)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout), connect=10.0),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # 同时声明两种，让服务端自行决定返回 JSON 还是 SSE
            "Accept": "application/json, text/event-stream",
        }
        if self.server.headers:
            headers.update({str(k): str(v) for k, v in self.server.headers.items()})
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        await self._post(build_message(method, params))

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        request_id = new_request_id()
        response = await self._post(build_message(method, params, request_id=request_id))
        content_type = (response.headers.get("content-type") or "").lower()
        if "text/event-stream" in content_type:
            return parse_sse_text(response.text, request_id)
        return extract_result(response.json(), request_id)

    async def _post(self, payload: Dict[str, Any]) -> httpx.Response:
        if not self._client:
            raise RuntimeError("HTTP 会话未初始化")
        response = await self._client.post(
            str(self.server.url), headers=self._headers(), json=payload
        )
        response.raise_for_status()
        # 首次响应可能带回会话 ID，需记住
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        return response


class SseSession:
    """sse 传输：GET 建长连接拿 endpoint，POST 发消息、从流上收响应。"""

    def __init__(self, server: McpServerConfig) -> None:
        self.server = server
        self._client: Optional[httpx.AsyncClient] = None
        self._stream_ctx = None
        self._response: Optional[httpx.Response] = None
        self._events = None
        self._endpoint: Optional[str] = None

    async def __aenter__(self) -> "SseSession":
        if not self.server.url:
            raise RuntimeError("sse 传输缺少服务地址（url）")
        timeout = normalize_timeout(self.server.timeout)
        # read=None：SSE 长连接不能有读超时，否则空闲即断
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout), connect=10.0, read=None),
            follow_redirects=True,
        )
        headers = {"Accept": "text/event-stream"}
        if self.server.headers:
            headers.update({str(k): str(v) for k, v in self.server.headers.items()})
        self._stream_ctx = self._client.stream(
            "GET", str(self.server.url), headers=headers
        )
        self._response = await self._stream_ctx.__aenter__()
        self._response.raise_for_status()
        self._events = iter_sse_events(self._response)
        await self._wait_endpoint(timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stream_ctx:
            await self._stream_ctx.__aexit__(exc_type, exc, tb)
            self._stream_ctx = None
        self._response = None
        self._events = None
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _wait_endpoint(self, timeout: int) -> None:
        """等待服务端下发 endpoint 事件，得到消息投递地址。"""

        async def _read_endpoint() -> str:
            async for event in self._events:
                if event.get("event") == "endpoint":
                    return event.get("data") or ""
            raise RuntimeError("SSE 连接已关闭，未收到 endpoint")

        raw = await asyncio.wait_for(_read_endpoint(), timeout=timeout)
        if not raw:
            raise RuntimeError("SSE endpoint 为空")
        # endpoint 可能是相对路径，需拼成绝对地址
        self._endpoint = urljoin(str(self.server.url), raw)

    async def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        await self._post(build_message(method, params))

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        request_id = new_request_id()
        await self._post(build_message(method, params, request_id=request_id))
        timeout = normalize_timeout(self.server.timeout)

        async def _read_result() -> Any:
            async for event in self._events:
                payload = load_sse_payload(
                    event.get("event") or "message", event.get("data") or ""
                )
                if isinstance(payload, dict) and payload.get("id") == request_id:
                    return extract_result(payload, request_id)
            raise RuntimeError("SSE 连接已关闭，未收到响应")

        return await asyncio.wait_for(_read_result(), timeout=timeout)

    async def _post(self, payload: Dict[str, Any]) -> None:
        if not self._client or not self._endpoint:
            raise RuntimeError("SSE 会话未初始化")
        headers = {"Content-Type": "application/json"}
        if self.server.headers:
            headers.update({str(k): str(v) for k, v in self.server.headers.items()})
        response = await self._client.post(self._endpoint, headers=headers, json=payload)
        response.raise_for_status()


def open_session(server: McpServerConfig):
    """按传输类型构造会话对象（异步上下文管理器）。"""
    transport = (server.transport or "stdio").strip().lower()
    if transport == "stdio":
        return StdioSession(server)
    if transport in {"streamable_http", "http", "streamable-http"}:
        return StreamableHttpSession(server)
    if transport == "sse":
        return SseSession(server)
    raise RuntimeError(f"不支持的 MCP 传输类型：{server.transport}")
