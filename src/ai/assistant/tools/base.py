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

from src.services.assistant_code_service import code_write_authorized
from ..api_gateway.contracts import (
    ActionEffect,
    ConfirmationMode,
    ResultSensitivity,
    default_confirmation,
    effect_to_permission,
    is_destructive,
)
from ..api_gateway.policy import resolve_api_operation
from ..security_gateway import (
    ToolPermission, can_execute, contains_forbidden_control_content, sanitize_output,
)

# 日志与任意 SQL 无法按字段可靠隔离流控和凭据，直接关闭入口。
_DISABLED_TOOLS = frozenset({"list_tokens", "list_log_files", "search_logs", "read_log_file"})

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
    # ── 风险三维（与 API 网关共用同一套契约） ──
    # 老工具未声明时按 permission 反推：WRITE→可逆写、READ_ONLY→安全读。
    # 显式声明后可区分「可逆写」与「不可逆写」，让删除类操作被单独识别。
    effect: Optional[ActionEffect] = None
    result_sensitivity: ResultSensitivity = ResultSensitivity.NORMAL
    confirmation: Optional[ConfirmationMode] = None

    @property
    def resolved_effect(self) -> ActionEffect:
        """取实际生效的副作用类别（显式声明优先，否则按 permission 反推）。"""
        if self.effect is not None:
            return self.effect
        if self.permission == ToolPermission.WRITE:
            return ActionEffect.REVERSIBLE_WRITE
        return ActionEffect.SAFE_READ

    @property
    def required_confirmation(self) -> ConfirmationMode:
        """取实际生效的确认强度（显式声明优先，否则按副作用推导）。"""
        return self.confirmation or default_confirmation(self.resolved_effect)

    @property
    def irreversible(self) -> bool:
        """是否为不可逆操作，供前端确认卡与提示词加重警示。"""
        return is_destructive(self.resolved_effect)

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
        if tool.name in _DISABLED_TOOLS:
            return
        if tool.name in self._tools:
            logger.warning(f"工具重复注册，覆盖：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        if name in _DISABLED_TOOLS:
            return None
        return self._tools.get(name)

    def all_tools(self) -> List[Tool]:
        return [tool for tool in self._tools.values() if tool.name not in _DISABLED_TOOLS]

    def openai_tools(self, include_write: bool = True, include_code: bool = False) -> List[Dict[str, Any]]:
        """
        导出 OpenAI tools 列表。
        - 危险工具永不导出。
        - include_write=False 时只导出只读工具。
        """
        result = []
        for tool in self.all_tools():
            if tool.name.startswith('code_') and not include_code:
                continue
            if tool.permission == ToolPermission.DANGEROUS:
                continue
            if not include_write and tool.name == "list_api_operations":
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
        写操作默认必须由确认端点领取单次令牌；当前管理员修复请求仅豁免两个代码写工具。
        """
        tool = self.get(name)
        if not tool:
            return {"ok": False, "error": f"未知工具：{name}"}

        if contains_forbidden_control_content(arguments or {}):
            return {"ok": False, "error": "流控与配额信息禁止 AI 访问"}
        permission = tool.permission
        confirmation = tool.required_confirmation
        if name == "call_api":
            operation = resolve_api_operation((arguments or {}).get("operation_id"))
            if operation is None:
                return {"ok": False, "error": "操作不在允许清单内"}
            permission = operation.permission
            confirmation = operation.required_confirmation
        allowed, _need_confirm = can_execute(permission)
        if not allowed:
            return {"ok": False, "error": f"工具 {name} 权限不允许执行"}
        if (permission == ToolPermission.WRITE or confirmation == ConfirmationMode.REQUIRED) and (
            (context or {}).get("confirmed_action") is not True
            and not (name in {'code_apply_patch', 'code_rollback_patch'} and code_write_authorized(context or {}))
        ):
            return {"ok": False, "error": "操作尚未获得本次确认"}

        try:
            data = await tool.executor(arguments or {}, context or {})
            if contains_forbidden_control_content(data):
                return {"ok": False, "error": "工具结果包含禁止向 AI 返回的信息"}
            # 禁止执行器通过豁免字段回填明文，所有凭据始终在出口脱敏。
            sanitized = sanitize_output(data)
            if isinstance(sanitized, dict):
                if "error" in sanitized and sanitized.get("ok") is not True:
                    return {"ok": False, "error": sanitized["error"]}
                if sanitized.get("ok") is False:
                    return {"ok": False, "error": sanitized.get("message", "工具执行失败")}
                sanitized.pop("__plaintext_exempt__", None)
                # 不可逆操作统一标注，供 AI 复述时明确告知后果无法撤销
                if tool.irreversible:
                    sanitized.setdefault("irreversible", True)
            return {"ok": True, "data": sanitized}
        except Exception as e:  # noqa: BLE001
            logger.error("工具执行失败 %s", name, exc_info=True)
            return {"ok": False, "error": "工具执行失败，请查看服务器日志"}


# 全局注册表实例
registry = ToolRegistry()
