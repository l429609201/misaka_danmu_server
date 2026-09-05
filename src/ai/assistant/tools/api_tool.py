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
            "选定 operationId 后用 call_api 执行。needsConfirmation 为 true 的操作，"
            "必须先用自然语言说明将要做什么并获得用户同意；irreversible 为 true 的"
            "（如删除）还要明确告知后果不可撤销。参数名必须与上面列出的完全一致。"
        ),
    }


async def _resolve_base_url(context: Dict[str, Any]) -> str:
    """
    解析用于拼接对外地址的服务基址。

    优先取「自定义域名」配置（与 Webhook 地址拼接口径一致）；
    未配置时返回占位串，由 AI 提示用户自行替换，避免给出错误地址。

    :param context: 工具执行上下文
    :return: 形如 http://example.com 的基址，或占位提示
    """
    config_manager = context.get("config_manager")
    if config_manager is None:
        return "http://<你的服务器地址>"
    try:
        domain = (await config_manager.get("webhookCustomDomain", "") or "").strip()
    except Exception:  # noqa: BLE001
        domain = ""
    if not domain:
        return "http://<你的服务器地址>"
    domain = domain.rstrip("/")
    if not domain.startswith(("http://", "https://")):
        domain = f"http://{domain}"
    return domain


async def _build_plaintext_exempt(
    operation: Any, data: Any, context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    按操作声明构造明文豁免回填内容。

    仅处理 policy 中显式登记 plaintext_exempt_fields 的操作；
    对 Token 创建场景额外拼好可直接填入播放器的完整地址。

    :param operation: 白名单操作契约
    :param data: 接口返回的原始数据
    :param context: 工具执行上下文
    :return: 待回填的明文字段字典；无豁免时为空字典
    """
    exempt_fields = getattr(operation, "plaintext_exempt_fields", ()) or ()
    if not exempt_fields or not isinstance(data, dict):
        return {}

    payload: Dict[str, Any] = {}
    for field_name in exempt_fields:
        value = data.get(field_name)
        if value not in (None, ""):
            payload[field_name] = value

    # Token 创建：把明文拼成播放器可直接使用的弹幕 API 地址
    token_value = payload.get("token")
    if token_value and operation.operation_id == "token.create":
        base_url = await _resolve_base_url(context)
        payload["danmakuApiUrl"] = f"{base_url}/api/v1/{token_value}"

    return payload


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

    app = context.get("app")
    current_user = context.get("current_user")
    if app is None or current_user is None:
        return {"error": "运行环境不完整，无法执行 API 调用（缺少应用实例或用户身份）"}

    try:
        result = await execute_operation(
            operation,
            app=app,
            current_user=current_user,
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

    # 明文豁免：交由 registry 在出口脱敏后回填，避免被统一打码
    exempt = await _build_plaintext_exempt(operation, data, context)
    if exempt:
        payload["__plaintext_exempt__"] = exempt
        payload["hint"] = (
            "请把 danmakuApiUrl 完整地址原样告诉用户，并提醒这是仅此一次展示的凭据，"
            "让用户立即保存。不要在后续对话里反复重复该明文。"
        )
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
            "⚠️ 涉及写入/修改/删除的操作，必须先用自然语言向用户说明你将要做什么，"
            "得到同意后再调用。\n\n"
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
