"""
御坂助手 · MCP 数据模型
------------------------------------------------------------
服务器配置与工具规格的 Pydantic 模型。

工具命名：MCP 工具名在全局可能与内部工具冲突，统一加 `mcp__{server}__`
前缀做命名空间隔离，调用时再还原为原始名发给服务端。

依赖导入统一置于文件头部，避免循环导入。
"""

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..security_gateway import ToolPermission

# 工具名前缀（OpenAI 要求工具名仅含 [a-zA-Z0-9_-]，长度 ≤64）
MCP_TOOL_PREFIX = "mcp__"
_NAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_TOOL_NAME_LEN = 64

# 支持的传输类型
TRANSPORT_STDIO = "stdio"
TRANSPORT_SSE = "sse"
TRANSPORT_HTTP = "streamable_http"
SUPPORTED_TRANSPORTS = (TRANSPORT_STDIO, TRANSPORT_SSE, TRANSPORT_HTTP)

# 权限模式：readonly=直接执行，write=需二次确认
PERMISSION_READONLY = "readonly"
PERMISSION_WRITE = "write"


def sanitize_name(raw: str) -> str:
    """把任意字符串规范成合法工具名片段。"""
    return _NAME_SAFE_RE.sub("_", str(raw or "").strip()) or "unnamed"


class McpServerConfig(BaseModel):
    """单个 MCP 服务器配置（持久化到 config 表的 JSON 数组元素）。"""

    name: str = Field(..., description="服务器名称，用于工具命名空间，需唯一")
    enabled: bool = Field(True, description="是否启用")
    transport: str = Field(TRANSPORT_STDIO, description="传输类型：stdio/sse/streamable_http")
    # stdio 专用
    command: str = Field("", description="stdio 启动命令，如 python")
    args: List[str] = Field(default_factory=list, description="stdio 命令参数")
    env: Dict[str, str] = Field(default_factory=dict, description="stdio 追加环境变量")
    # http/sse 专用
    url: str = Field("", description="服务地址（sse/streamable_http 必填）")
    headers: Dict[str, str] = Field(default_factory=dict, description="HTTP 请求头（如鉴权）")
    # 通用
    timeout: int = Field(30, description="单次请求超时（秒），范围 1-600")
    permission: str = Field(
        PERMISSION_WRITE,
        description="该服务器工具的权限级别：readonly 直接执行 / write 需用户确认",
    )
    description: str = Field("", description="备注说明，仅供界面展示")

    def tool_permission(self) -> ToolPermission:
        """映射到项目内部的权限枚举。"""
        if (self.permission or "").strip().lower() == PERMISSION_READONLY:
            return ToolPermission.READ_ONLY
        # 默认按写处理：MCP 工具的副作用无法从协议自动判断，从严
        return ToolPermission.WRITE

    def namespace(self) -> str:
        """工具名命名空间片段。"""
        return sanitize_name(self.name)


class McpToolSpec(BaseModel):
    """从 MCP 服务器发现的单个工具。"""

    server_name: str = Field(..., description="所属服务器名称")
    original_name: str = Field(..., description="MCP 服务端的原始工具名")
    agent_tool_name: str = Field(..., description="暴露给 LLM 的带前缀工具名")
    description: str = Field("", description="工具描述")
    parameters: Dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="JSON Schema 参数定义",
    )
    permission: ToolPermission = Field(
        ToolPermission.WRITE, description="权限级别（继承服务器配置）"
    )

    model_config = {"arbitrary_types_allowed": True}

    def to_openai_schema(self) -> Dict[str, Any]:
        """转 OpenAI function calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.agent_tool_name,
                "description": self.description or self.original_name,
                "parameters": self.parameters,
            },
        }


def build_agent_tool_name(server_name: str, tool_name: str) -> str:
    """拼装带命名空间的工具名，超长时截断尾部保证 ≤64 字符。"""
    prefix = f"{MCP_TOOL_PREFIX}{sanitize_name(server_name)}__"
    safe_tool = sanitize_name(tool_name)
    budget = _MAX_TOOL_NAME_LEN - len(prefix)
    if budget <= 0:
        # 服务器名本身过长，退化为仅保留工具名
        return safe_tool[:_MAX_TOOL_NAME_LEN]
    return f"{prefix}{safe_tool[:budget]}"


def parse_tool_spec(
    server: McpServerConfig, raw: Dict[str, Any]
) -> Optional[McpToolSpec]:
    """把 MCP tools/list 返回的单项转成 McpToolSpec。名称缺失则丢弃。"""
    if not isinstance(raw, dict):
        return None
    original = str(raw.get("name") or "").strip()
    if not original:
        return None
    schema = raw.get("inputSchema") or raw.get("input_schema") or {}
    if not isinstance(schema, dict) or not schema:
        schema = {"type": "object", "properties": {}}
    return McpToolSpec(
        server_name=server.name,
        original_name=original,
        agent_tool_name=build_agent_tool_name(server.name, original),
        description=str(raw.get("description") or "").strip(),
        parameters=schema,
        permission=server.tool_permission(),
    )
