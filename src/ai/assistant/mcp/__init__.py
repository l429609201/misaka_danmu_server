"""御坂助手 · MCP 客户端包

对接外部 MCP（Model Context Protocol）服务器，把其暴露的工具动态接入
助手的 function calling 能力。

设计要点：
- 工具不进入静态 `registry`，由 `McpManager` 独立维护（方案 B1），
  agent 组装 tools 时合并、执行时按 `mcp__` 前缀路由。
- 支持 stdio / sse / streamable_http 三种传输。
- 权限按服务器配置声明：readonly 始终可用，write 仅在调用方开启
  include_write_tools 时才暴露给 LLM。
"""

from .manager import MCP_SERVERS_CONFIG_KEY, McpManager
from .schemas import (
    MCP_TOOL_PREFIX,
    PERMISSION_READONLY,
    PERMISSION_WRITE,
    SUPPORTED_TRANSPORTS,
    TRANSPORT_HTTP,
    TRANSPORT_SSE,
    TRANSPORT_STDIO,
    McpServerConfig,
    McpToolSpec,
)

__all__ = [
    "McpManager",
    "MCP_SERVERS_CONFIG_KEY",
    "McpServerConfig",
    "McpToolSpec",
    "MCP_TOOL_PREFIX",
    "SUPPORTED_TRANSPORTS",
    "TRANSPORT_STDIO",
    "TRANSPORT_SSE",
    "TRANSPORT_HTTP",
    "PERMISSION_READONLY",
    "PERMISSION_WRITE",
]
