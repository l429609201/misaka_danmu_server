"""
御坂助手 · MCP 管理器
------------------------------------------------------------
负责 MCP 服务器配置的读写、工具发现（带缓存）、工具调用路由。

设计取舍（方案 B1）：MCP 工具不进入静态 `registry`，由本管理器独立维护，
agent 在组装 tools 时合并、执行时路由。这样 registry 保持同步纯净，
MCP 服务器不可达时只是少几个工具，不影响内部工具可用性。

依赖导入统一置于文件头部，避免循环导入。
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from src.db import ConfigManager

from ..security_gateway import ToolPermission, sanitize_output
from .protocol import build_initialize_params
from .schemas import (
    MCP_TOOL_PREFIX,
    McpServerConfig,
    McpToolSpec,
    parse_tool_spec,
)
from .transport import open_session

logger = logging.getLogger(__name__)

# 配置持久化的 key（值为 JSON 数组字符串）
MCP_SERVERS_CONFIG_KEY = "assistantMcpServers"
# 工具发现结果缓存时长（秒）。MCP 服务器工具列表不常变，避免每轮对话都连一次
_DISCOVERY_TTL = 300


class McpManager:
    """MCP 服务器与工具的统一管理入口。"""

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager
        self.logger = logging.getLogger(self.__class__.__name__)
        # 工具发现缓存：{server_name: (过期时间戳, [McpToolSpec])}
        self._cache: Dict[str, tuple[float, List[McpToolSpec]]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------

    async def get_servers(self) -> List[McpServerConfig]:
        """读取全部 MCP 服务器配置。解析失败返回空列表，不影响主流程。"""
        raw = await self.config_manager.get(MCP_SERVERS_CONFIG_KEY, "[]")
        try:
            items = json.loads(raw or "[]")
        except (TypeError, ValueError):
            self.logger.warning("MCP 服务器配置不是合法 JSON，已按空列表处理")
            return []
        if not isinstance(items, list):
            return []
        servers: List[McpServerConfig] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                servers.append(McpServerConfig(**item))
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"跳过非法的 MCP 服务器配置项：{e}")
        return servers

    async def save_servers(self, servers: List[McpServerConfig]) -> None:
        """保存全部 MCP 服务器配置，并清空发现缓存。"""
        payload = json.dumps(
            [s.model_dump() for s in servers], ensure_ascii=False
        )
        await self.config_manager.setValue(MCP_SERVERS_CONFIG_KEY, payload)
        await self.invalidate_cache()

    async def get_server(self, name: str) -> Optional[McpServerConfig]:
        """按名称取单个服务器配置。"""
        for server in await self.get_servers():
            if server.name == name:
                return server
        return None

    async def invalidate_cache(self, server_name: Optional[str] = None) -> None:
        """清除工具发现缓存。不传服务器名则全清。"""
        async with self._lock:
            if server_name:
                self._cache.pop(server_name, None)
            else:
                self._cache.clear()

    # ------------------------------------------------------------------
    # 工具发现
    # ------------------------------------------------------------------

    async def list_server_tools(
        self, server: McpServerConfig, *, use_cache: bool = True
    ) -> List[McpToolSpec]:
        """连接单个服务器发现工具。失败抛异常，由调用方决定容错策略。"""
        if use_cache:
            async with self._lock:
                cached = self._cache.get(server.name)
                if cached and cached[0] > time.monotonic():
                    return cached[1]

        async with open_session(server) as session:
            await session.request("initialize", build_initialize_params())
            # MCP 协议要求 initialize 后发 initialized 通知
            await session.notify("notifications/initialized")
            result = await session.request("tools/list")

        raw_tools = (result or {}).get("tools") if isinstance(result, dict) else None
        specs: List[McpToolSpec] = []
        for raw in raw_tools or []:
            spec = parse_tool_spec(server, raw)
            if spec:
                specs.append(spec)

        async with self._lock:
            self._cache[server.name] = (time.monotonic() + _DISCOVERY_TTL, specs)
        return specs

    async def list_enabled_tool_specs(
        self, *, include_write: bool = True
    ) -> List[McpToolSpec]:
        """并发发现所有已启用服务器的工具。单个服务器失败只记日志并跳过。"""
        servers = [s for s in await self.get_servers() if s.enabled]
        if not servers:
            return []

        results = await asyncio.gather(
            *(self.list_server_tools(s) for s in servers),
            return_exceptions=True,
        )
        specs: List[McpToolSpec] = []
        for server, result in zip(servers, results):
            if isinstance(result, BaseException):
                self.logger.warning(f"MCP 服务器 {server.name} 工具发现失败：{result}")
                continue
            for spec in result:
                if not include_write and spec.permission == ToolPermission.WRITE:
                    continue
                specs.append(spec)
        return specs

    # ------------------------------------------------------------------
    # 工具调用
    # ------------------------------------------------------------------

    @staticmethod
    def is_mcp_tool_name(name: str) -> bool:
        """判断工具名是否属于 MCP 命名空间。"""
        return str(name or "").startswith(MCP_TOOL_PREFIX)

    async def resolve_tool(
        self,
        agent_tool_name: str,
        known_specs: Optional[List[McpToolSpec]] = None,
    ) -> Optional[McpToolSpec]:
        """按暴露给 LLM 的工具名反查工具规格。

        传入 known_specs 可复用调用方已发现的结果，避免在工具调用的关键路径上
        因缓存刚过期而重新连接所有服务器。
        """
        specs = known_specs if known_specs is not None else await self.list_enabled_tool_specs()
        for spec in specs:
            if spec.agent_tool_name == agent_tool_name:
                return spec
        return None

    async def call_tool(
        self,
        agent_tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        known_specs: Optional[List[McpToolSpec]] = None,
    ) -> Dict[str, Any]:
        """
        调用 MCP 工具，返回与 registry.execute 一致的 {ok, data|error} 结构。
        why 保持同构：agent 的结果回灌逻辑无需分支处理两类工具。
        """
        spec = await self.resolve_tool(agent_tool_name, known_specs)
        if not spec:
            return {"ok": False, "error": f"未知的 MCP 工具：{agent_tool_name}"}

        server = await self.get_server(spec.server_name)
        if not server or not server.enabled:
            return {"ok": False, "error": f"MCP 服务器 {spec.server_name} 未启用"}

        try:
            async with open_session(server) as session:
                await session.request("initialize", build_initialize_params())
                await session.notify("notifications/initialized")
                result = await session.request(
                    "tools/call",
                    {"name": spec.original_name, "arguments": arguments or {}},
                )
        except asyncio.TimeoutError:
            self.logger.error(f"MCP 工具 {agent_tool_name} 调用超时")
            return {"ok": False, "error": "MCP 工具调用超时"}
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"MCP 工具 {agent_tool_name} 调用失败：{e}", exc_info=True)
            return {"ok": False, "error": f"MCP 工具调用出错：{e}"}

        # MCP 约定 isError=True 表示工具内部失败（非协议错误）
        if isinstance(result, dict) and result.get("isError"):
            text = extract_content_text(result.get("content"))
            return {"ok": False, "error": text or "MCP 工具执行失败"}

        data = normalize_call_result(result)
        # 数据出口脱敏：与内部工具同一道防线，密钥绝不回灌给 AI
        return {"ok": True, "data": sanitize_output(data)}

    async def test_server(self, server: McpServerConfig) -> Dict[str, Any]:
        """连通性测试：返回是否成功与发现到的工具名列表，供配置界面使用。"""
        try:
            specs = await self.list_server_tools(server, use_cache=False)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "tools": []}
        return {
            "ok": True,
            "tools": [
                {"name": s.original_name, "description": s.description} for s in specs
            ],
        }


def extract_content_text(content: Any) -> str:
    """把 MCP content 数组里的 text 片段拼成一段文本。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(p for p in parts if p)


def normalize_call_result(result: Any) -> Any:
    """
    归一化 tools/call 返回值，供 LLM 消费。

    MCP 返回 {content: [...], structuredContent: {...}}。优先用结构化内容，
    其次把 text 片段拼成文本；非 text 类型（图片/资源）保留原始 dict 以免丢信息。
    """
    if not isinstance(result, dict):
        return result

    structured = result.get("structuredContent")
    if structured not in (None, {}, []):
        return structured

    content = result.get("content")
    text = extract_content_text(content)
    non_text = [
        item
        for item in (content or [])
        if isinstance(item, dict) and item.get("type") != "text"
    ]
    if text and not non_text:
        # 纯文本结果：尝试解析成 JSON，让 LLM 拿到结构而非字符串
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text
    if text or non_text:
        return {"text": text, "attachments": non_text}
    return result
