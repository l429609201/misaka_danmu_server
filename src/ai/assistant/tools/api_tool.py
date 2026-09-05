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
from typing import Any, Dict, Optional

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


async def _resolve_custom_domain(context: Dict[str, Any]) -> Optional[str]:
    """
    读取用户在「弹幕 → Token 管理 → 自定义域名」配置的对外域名。

    统一走 src.utils.image_utils.get_custom_domain（配置键 custom_api_domain），
    与海报外链、通知外链、命令指令等所有拼接外联地址的业务保持同一口径，
    不再自己读 webhookCustomDomain 这种不一致的键。

    :param context: 工具执行上下文
    :return: 规范化后的域名（已去尾部斜杠，含 http/https 前缀）；未配置或不合规返回 None
    """
    config_manager = context.get("config_manager")
    if config_manager is None:
        return None
    # 延迟导入：避免工具层与 utils 之间的模块级耦合
    from src.utils.image_utils import get_custom_domain
    try:
        return await get_custom_domain(config_manager)
    except Exception:  # noqa: BLE001
        return None


async def _build_plaintext_exempt(
    operation: Any, data: Any, context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    按操作声明构造明文豁免回填内容。

    仅处理 policy 中显式登记 plaintext_exempt_fields 的操作；
    对 Token 创建场景，若已配置自定义域名则拼好完整可用地址，
    否则回传裸 token 并提示用户去配置域名或自行替换。

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

    # Token 创建：尽量拼成播放器可直接使用的弹幕 API 地址
    token_value = payload.get("token")
    if token_value and operation.operation_id == "token.create":
        domain = await _resolve_custom_domain(context)
        if domain:
            # 已配置自定义域名 —— 给出可直接复制粘贴的完整地址
            payload["danmakuApiUrl"] = f"{domain}/api/v1/{token_value}"
            payload["danmakuApiPath"] = f"/api/v1/{token_value}"
        else:
            # 未配置 —— 只给相对路径，并明确告诉 AI 引导用户去配置或自行补域名，
            # 绝不编造域名（此前用错配置键导致展示成占位串，已修正）
            payload["danmakuApiPath"] = f"/api/v1/{token_value}"
            payload["domainNotConfigured"] = True

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
        if exempt.get("domainNotConfigured"):
            # 未配置自定义域名：只有相对路径，必须引导用户去配置或自行补全域名
            payload["hint"] = (
                "系统还没有配置「自定义域名」，所以无法拼出完整地址。请这样回复用户：\n"
                "1. 先把弹幕接口路径原样发给用户（danmakuApiPath，形如 /api/v1/xxx），"
                "并说明这是仅此一次展示的凭据，务必立即保存；\n"
                "2. 再提示用户：完整地址 = 你的服务器地址 + 该路径；\n"
                "3. 建议用户到「弹幕 → Token 管理 → 自定义域名」填写公网 HTTPS 域名，"
                "之后系统就能自动拼出可直接复制的完整地址。\n"
                "不要自己编造或猜测服务器域名。"
            )
        else:
            # 已配置域名：给出可直接复制的完整地址
            payload["hint"] = (
                "请把 danmakuApiUrl 完整地址【单独成行、原样】发给用户，方便直接复制粘贴到播放器；"
                "并提醒这是仅此一次展示的凭据，让用户立即保存。"
                "不要在后续对话里反复重复该明文。"
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
