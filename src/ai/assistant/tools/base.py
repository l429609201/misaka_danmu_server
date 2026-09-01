"""
御坂助手 · 工具注册表框架（P2）
------------------------------------------------------------
定义 Tool 结构与全局注册表。每个工具 = 名称 + 描述 + JSON Schema 参数
+ 权限级别 + 异步执行函数。

- 转 OpenAI tools 格式供 function calling。
- 执行前经权限校验（只读与写操作放行 / 危险级禁止），写操作的风险确认
  由 agent 在对话中自然完成。
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..security_gateway import ToolPermission, can_execute, sanitize_output

logger = logging.getLogger(__name__)

# 工具执行函数签名：async (arguments: dict, context: dict) -> dict
ToolExecutor = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass
class Tool:
    """单个工具定义。"""
    name: str
    description: str
    parameters: Dict[str, Any]          # JSON Schema（OpenAI function parameters）
    permission: ToolPermission
    executor: ToolExecutor
    # 供前端展示的中文动作描述模板（可选），如 "正在搜索媒体…"
    running_label: str = ""

    def to_openai_schema(self) -> Dict[str, Any]:
        """转 OpenAI function calling 的 tool 定义。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表：集中登记与查询。"""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            logger.warning(f"工具重复注册，覆盖：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all_tools(self) -> List[Tool]:
        return list(self._tools.values())

    def openai_tools(self, include_write: bool = True) -> List[Dict[str, Any]]:
        """
        导出 OpenAI tools 列表。
        - 危险工具永不导出。
        - include_write=False 时只导出只读工具。
        """
        result = []
        for tool in self._tools.values():
            if tool.permission == ToolPermission.DANGEROUS:
                continue
            if not include_write and tool.permission == ToolPermission.WRITE:
                continue
            result.append(tool.to_openai_schema())
        return result

    async def execute(
        self, name: str, arguments: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        执行工具（经权限校验）。返回 {ok, data|error}。
        WRITE 工具在此不做确认拦截——确认由上层 agent 在调用前处理（P3）。
        """
        tool = self.get(name)
        if not tool:
            return {"ok": False, "error": f"未知工具：{name}"}

        allowed, _need_confirm = can_execute(tool.permission)
        if not allowed:
            return {"ok": False, "error": f"工具 {name} 权限不允许执行"}

        try:
            data = await tool.executor(arguments or {}, context or {})
            # 数据出口脱敏：密钥/token 类字段一律 ***，绝不回灌给 AI（最后防线）
            return {"ok": True, "data": sanitize_output(data)}
        except Exception as e:  # noqa: BLE001
            logger.error(f"工具执行失败 {name}: {e}", exc_info=True)
            return {"ok": False, "error": f"工具执行出错：{e}"}


# 全局注册表实例
registry = ToolRegistry()
