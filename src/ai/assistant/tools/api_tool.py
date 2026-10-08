"""
御坂助手 · API 网关工具（call_api / list_api_operations）
------------------------------------------------------------
对齐 MoviePilot v3 `app/agent/tools/impl/api.py` 的单一网关设计：
不再为每个业务功能手写一个工具，而是由 AI 提交 operation_id + 结构化参数，
网关解析成固定的方法与路径，走内部 ASGI 调用复用路由层全部业务校验。

带来的差别：后端新增接口后，只需在 api_gateway/policy.py 加一条白名单，
助手立即可用，无需再写 Python 工具函数。

安全边界：
- AI 只能提交 operation_id，不能提交 URL、HTTP 方法、认证头或令牌。
- 未在白名单登记的 operation 一律拒绝（默认拒绝原则）。
- 写操作权限为 WRITE，由 agent 先向用户说明再执行。
- 返回值仍走 registry 的 sanitize_output 出口脱敏。
"""

import logging
from typing import Any, Dict

from ..api_gateway import (
    ApiExecutionError,
    ConfirmationMode,
    EFFECT_LABELS,
    execute_operation,
    is_destructive,
    list_exposed_operations,
    resolve_api_operation,
)
from ..security_gateway import ToolPermission
from .base import Tool, registry

logger = logging.getLogger(__name__)


def _build_operation_catalog() -> str:
    """把白名单操作拼成给 AI 看的操作目录（进入工具描述）。"""
    lines = []
    for op in list_exposed_operations():
        label = EFFECT_LABELS.get(op.effect, "操作")
        mark = "⚠️不可逆" if is_destructive(op.effect) else label
        lines.append(f"- `{op.operation_id}`（{mark}）：{op.summary}")
    return "\n".join(lines)


async def _list_api_operations(
    arguments: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    """列出全部可用 operation 及其参数说明，供 AI 选择正确的操作与参数。"""
    keyword = (arguments.get("keyword") or "").strip().lower()
    items = []
    for op in list_exposed_operations():
        if keyword and keyword not in op.operation_id.lower() and keyword not in op.summary.lower():
            continue
        items.append({
            "operationId": op.operation_id,
            "summary": op.summary,
            # 风险三维：让 AI 据此决定说明措辞的轻重，而非只知道"是写操作"
            "effect": EFFECT_LABELS.get(op.effect, "操作"),
            "irreversible": is_destructive(op.effect),
            "needsConfirmation": op.required_confirmation is ConfirmationMode.REQUIRED,
            "resultSensitivity": op.result_sensitivity.value,
            "pathParams": op.path_params or None,
            "queryParams": op.query_params or None,
            "bodyFields": op.body_fields or None,
        })
    return {
        "total": len(items),
        "operations": items,
        "hint": (
            "选定 operationId 后用 call_api 执行。needsConfirmation 为 true 的操作会先暂停，"
            "由用户在界面确认后才执行；irreversible 为 true 的操作还应告知后果不可撤销。"
            "参数名必须与上面列出的完全一致。"
        ),
    }


async def _call_api(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """执行一个白名单 API 操作（内部 ASGI 调用，复用路由层校验）。"""
    operation_id = (arguments.get("operation_id") or "").strip()
    if not operation_id:
        return {"error": "缺少 operation_id，可先调 list_api_operations 查看可用操作"}

    operation = resolve_api_operation(operation_id)
    if operation is None:
        return {
            "error": f"操作 `{operation_id}` 不在允许清单内",
            "hint": "调 list_api_operations 查看当前可用的 operationId，不要自行猜测或拼造。",
        }

    for field_name, allowed_keys in (
        ("path_params", operation.path_params),
        ("query", operation.query_params),
        ("body", operation.body_fields),
    ):
        supplied = arguments.get(field_name)
        if supplied is None:
            supplied = {}
        if not isinstance(supplied, dict):
            return {"error": f"{field_name} 必须是对象"}
        unexpected = set(supplied) - set(allowed_keys)
        if unexpected:
            return {"error": f"{field_name} 含未开放字段：{', '.join(sorted(unexpected))}"}
    if set(arguments) - {"operation_id", "path_params", "query", "body"}:
        return {"error": "存在未开放的顶层参数"}

    app = context.get("app")
    current_user = context.get("current_user")
    authorization = context.get("authorization")
    if app is None or current_user is None or not authorization:
        return {"error": "运行环境不完整，无法执行 API 调用（缺少用户认证凭据）"}

    try:
        result = await execute_operation(
            operation,
            app=app,
            authorization=authorization,
            path_params=arguments.get("path_params") or {},
            query=arguments.get("query") or {},
            body=arguments.get("body"),
        )
    except ApiExecutionError as e:
        return {"error": str(e)}

    if not result.get("ok"):
        return {
            "error": result.get("error") or "接口调用失败",
            "status": result.get("status"),
            "operationId": operation.operation_id,
        }

    data = result.get("data")
    payload: Dict[str, Any] = {
        "ok": True,
        "operationId": operation.operation_id,
        "status": result.get("status"),
        "data": data,
    }
    if operation.success_hint:
        payload["message"] = operation.success_hint
    # 不可逆操作在结果里标明，供 AI 在复述时明确告知用户后果已生效且无法撤销
    if is_destructive(operation.effect):
        payload["irreversible"] = True

    return payload


def register_api_gateway_tools() -> None:
    """注册 API 网关工具：list_api_operations（只读）+ call_api（按操作分级）。"""
    registry.register(Tool(
        name="list_api_operations",
        description=(
            "列出助手可以代为执行的系统操作清单（operationId + 参数说明）。"
            "当用户要求你「帮我创建/修改/删除某项配置」，而你不确定有没有对应操作、"
            "或不确定参数怎么传时，先调此工具查询，不要直接回答做不到。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "按关键词过滤（如 token、配置），留空则返回全部",
                },
            },
        },
        permission=ToolPermission.READ_ONLY,
        executor=_list_api_operations,
        running_label="正在查询可用操作",
    ))
    registry.register(Tool(
        name="call_api",
        description=(
            "执行一个系统操作（走内部接口，复用后台的全部校验规则）。"
            "你只能提交 operation_id 与结构化参数，不能提交 URL 或 HTTP 方法。\n"
            "涉及写入、修改、删除的操作会先暂停并等待用户在确认卡上明确授权。\n\n"
            "当前可用操作：\n" + _build_operation_catalog()
        ),
        parameters={
            "type": "object",
            "properties": {
                "operation_id": {
                    "type": "string",
                    "description": "操作标识，必须来自 list_api_operations 返回的清单，不得自行拼造",
                },
                "path_params": {
                    "type": "object",
                    "description": "路径参数，如 {\"token_id\": 3}",
                },
                "query": {
                    "type": "object",
                    "description": "查询参数",
                },
                "body": {
                    "type": "object",
                    "description": "请求体字段，字段名须与操作声明的 bodyFields 一致",
                },
            },
            "required": ["operation_id"],
        },
        permission=ToolPermission.WRITE,
        executor=_call_api,
        running_label="正在执行系统操作",
    ))
