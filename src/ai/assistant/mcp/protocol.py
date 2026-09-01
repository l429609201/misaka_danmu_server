"""
御坂助手 · MCP 协议基础层
------------------------------------------------------------
JSON-RPC 2.0 消息构造与解析，SSE 事件流解析。
不含传输实现，仅提供协议原语，供 transport.py 复用。

依赖导入统一置于文件头部，避免循环导入。
"""

import json
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, Optional

logger = logging.getLogger(__name__)

# MCP 协议版本（与 MoviePilot v3 对齐）
MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_CLIENT_NAME = "Misaka Danmu Assistant"
MCP_CLIENT_VERSION = "1.0.0"

# 超时边界：最小 1 秒，最大 600 秒
_TIMEOUT_MIN = 1
_TIMEOUT_MAX = 600
DEFAULT_MCP_TIMEOUT = 30


def new_request_id() -> str:
    """生成 JSON-RPC 请求 ID。"""
    return uuid.uuid4().hex


def normalize_timeout(value: Any) -> int:
    """规范化超时时间，钳制到 [1, 600] 秒。"""
    try:
        timeout = int(value or DEFAULT_MCP_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_MCP_TIMEOUT
    return min(max(timeout, _TIMEOUT_MIN), _TIMEOUT_MAX)


def build_message(
    method: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构造 JSON-RPC 2.0 消息。不传 request_id 即为通知（无需响应）。"""
    payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        payload["id"] = request_id
    if params is not None:
        payload["params"] = params
    return payload


def build_initialize_params() -> Dict[str, Any]:
    """构造 initialize 请求参数。"""
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {
            "name": MCP_CLIENT_NAME,
            "version": MCP_CLIENT_VERSION,
        },
    }


def raise_for_error(payload: Any) -> None:
    """检查 JSON-RPC 响应中的 error 字段，有则抛异常。"""
    if not isinstance(payload, dict):
        return
    error = payload.get("error")
    if not error:
        return
    message = error.get("message") if isinstance(error, dict) else error
    raise RuntimeError(f"MCP 服务返回错误：{message}")


def extract_result(payload: Any, request_id: str) -> Any:
    """从 JSON-RPC 响应提取 result，校验 ID 匹配。"""
    if not isinstance(payload, dict):
        raise RuntimeError("MCP 响应不是合法的 JSON 对象")
    if payload.get("id") != request_id:
        raise RuntimeError("MCP 响应 ID 与请求不匹配")
    raise_for_error(payload)
    return payload.get("result")


def load_sse_payload(event_name: str, data: str) -> Optional[Dict[str, Any]]:
    """解析 SSE data 字段中的 JSON-RPC 消息。非 message 事件返回 None。"""
    if event_name not in {"message", "messages"}:
        return None
    try:
        payload = json.loads(data)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


async def iter_sse_events(response) -> AsyncGenerator[Dict[str, str], None]:
    """按 SSE 格式迭代 httpx 流式响应，逐个产出 {event, data}。"""
    event_name = "message"
    data_lines: list[str] = []
    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            # 空行代表一个事件结束
            if data_lines:
                yield {"event": event_name, "data": "\n".join(data_lines)}
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            # SSE 注释行，跳过
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value or "message"
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield {"event": event_name, "data": "\n".join(data_lines)}


def parse_sse_text(text: str, request_id: str) -> Any:
    """从非流式 SSE 文本响应中提取匹配 request_id 的结果。"""
    event_name = "message"
    data_lines: list[str] = []

    def _try_match() -> Any:
        payload = load_sse_payload(event_name, "\n".join(data_lines))
        if isinstance(payload, dict) and payload.get("id") == request_id:
            return extract_result(payload, request_id)
        return None

    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                matched = _try_match()
                if matched is not None:
                    return matched
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value or "message"
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        matched = _try_match()
        if matched is not None:
            return matched
    raise RuntimeError("MCP SSE 响应中未找到匹配的请求结果")
