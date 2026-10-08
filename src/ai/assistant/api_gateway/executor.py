"""通过白名单路由执行助手操作，内部请求按原始用户令牌鉴权。"""

from __future__ import annotations

import logging
from urllib.parse import quote
from typing import Any, Dict, Optional

import httpx

from .policy import ApiOperation

logger = logging.getLogger(__name__)

# 单次内部调用超时（秒）。业务接口若更慢，应改为提交后台任务而非同步等待。
_REQUEST_TIMEOUT_SECONDS = 30.0
# 返回体截断上限，防止超大响应挤爆 LLM 上下文
_MAX_RESPONSE_CHARS = 8000
# 列表类响应的条目预览上限：超出则截断，但总数由 collection 元数据保留
_MAX_LIST_PREVIEW_ITEMS = 30
# 分页/计数类响应头 → 结构化字段名（对齐 MoviePilot 的集合元数据投影）
_COLLECTION_HEADERS = {
    "x-total-count": "total_count",
    "x-result-count": "result_count",
    "x-page": "page",
    "x-page-size": "page_size",
}


class ApiExecutionError(Exception):
    """网关执行阶段的可预期错误（环境缺失、超时等）。"""


def _fill_path(operation: ApiOperation, path_params: Dict[str, Any]) -> str:
    """
    用路径参数填充 URL 模板，并校验必填项齐全。

    :param operation: 白名单操作契约
    :param path_params: AI 提交的路径参数
    :return: 可直接请求的完整路径
    """
    path = operation.full_path
    for name in operation.path_params:
        if name not in path_params or path_params[name] in (None, ""):
            raise ApiExecutionError(f"缺少路径参数：{name}")
        # 路径参数一律转字符串并做基础清理，避免注入额外路径层级
        raw = str(path_params[name]).strip()
        if "/" in raw or ".." in raw:
            raise ApiExecutionError(f"路径参数 {name} 含非法字符")
        path = path.replace(f"{{{name}}}", quote(raw, safe=""))
    if "{" in path:
        raise ApiExecutionError(f"路径模板未完全填充：{path}")
    return path


def _summarize_body(response: httpx.Response) -> Any:
    """
    解析响应体：优先 JSON，退化为文本，并做长度截断。

    :param response: 内部调用得到的响应
    :return: 结构化数据或截断后的文本
    """
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        text = response.text or ""
        if len(text) > _MAX_RESPONSE_CHARS:
            return text[:_MAX_RESPONSE_CHARS] + "…（已截断）"
        return text


def _attach_collection_metadata(payload: Any, response: httpx.Response) -> Any:
    """
    为列表类响应补上集合元数据（总数、分页），并把元数据放在数据之前。

    why：列表条目可能因上下文预算被截断，若总数只能从条目个数推断，
    AI 会把「截断后的 30 条」当成「一共 30 条」答给用户。把精确总数
    单独投影出来并前置，即使条目被截断，总数依然准确。

    :param payload: 已解析的响应体
    :param response: 原始响应（用于读取分页响应头）
    :return: 补充元数据后的响应体
    """
    collection: Dict[str, int] = {}

    # 1. 优先采信后端显式给出的分页响应头
    normalized = {str(k).lower(): v for k, v in response.headers.items()}
    for header_name, field_name in _COLLECTION_HEADERS.items():
        raw = normalized.get(header_name)
        if raw is None:
            continue
        try:
            collection[field_name] = int(raw)
        except (TypeError, ValueError):
            continue

    # 2. 裸数组响应：统计真实条目数，并按预览上限截断
    if isinstance(payload, list):
        total = len(payload)
        collection.setdefault("total_count", total)
        items = payload[:_MAX_LIST_PREVIEW_ITEMS]
        result: Dict[str, Any] = {"collection": collection, "items": items}
        if total > len(items):
            result["truncated"] = True
            result["hint"] = (
                f"共 {total} 条，此处仅展示前 {len(items)} 条。"
                f"回答总数时请用 collection.total_count，不要数 items 的个数。"
            )
        return result

    # 3. 对象响应：仅在有元数据时前置补充，不改动原有字段
    if isinstance(payload, dict) and collection:
        merged: Dict[str, Any] = {"collection": collection}
        for key, value in payload.items():
            if key != "collection":
                merged[key] = value
        return merged

    return payload


def _extract_error_message(payload: Any, status_code: int) -> str:
    """
    从 FastAPI 的错误响应中提取可读中文提示。

    :param payload: 已解析的响应体
    :param status_code: HTTP 状态码
    :return: 面向用户的错误说明
    """
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        if isinstance(detail, list) and detail:
            # Pydantic 校验错误：拼出字段与原因
            parts = []
            for item in detail:
                if not isinstance(item, dict):
                    continue
                loc = ".".join(str(x) for x in (item.get("loc") or []) if x != "body")
                msg = item.get("msg") or ""
                parts.append(f"{loc}: {msg}" if loc else msg)
            if parts:
                return "参数校验失败 —— " + "；".join(parts)
    return f"接口返回 HTTP {status_code}"


async def execute_operation(
    operation: ApiOperation,
    *,
    app: Any,
    authorization: str,
    path_params: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, Any]] = None,
    body: Any = None,
) -> Dict[str, Any]:
    """
    以已认证身份执行一次白名单 API 操作。

    :param operation: 已解析的白名单操作契约
    :param app: FastAPI 应用实例（从 request.app 传入）
    :param authorization: 当前 Web 请求的 Bearer 鉴权头
    :param path_params: 路径参数
    :param query: 查询参数
    :param body: JSON 请求体
    :return: {"ok": bool, "status": int, "data"|"error": ...}
    """
    if app is None:
        raise ApiExecutionError("运行环境不完整：缺少应用实例")
    scheme, separator, credential = (authorization or "").partition(" ")
    if (scheme.lower() != "bearer" or not separator or not credential
            or any(ch.isspace() for ch in credential)):
        raise ApiExecutionError("当前渠道没有可透传的登录凭据，无法调用站内接口")

    url_path = _fill_path(operation, path_params or {})
    clean_query = {
        k: v for k, v in (query or {}).items() if v is not None and v != ""
    }

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://misaka-assistant.internal",
            timeout=_REQUEST_TIMEOUT_SECONDS,
        ) as client:
            response = await client.request(
                operation.method,
                url_path,
                params=clean_query or None,
                json=body if body is not None else None,
                headers={"Authorization": authorization},
            )
    except httpx.TimeoutException as exc:
        raise ApiExecutionError(f"接口调用超时（超过 {int(_REQUEST_TIMEOUT_SECONDS)} 秒）") from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("御坂助手 API 网关内部调用失败 %s %s: %s",
                     operation.method, url_path, exc, exc_info=True)
        raise ApiExecutionError(f"接口调用失败：{exc}") from exc

    payload = _summarize_body(response)
    if response.status_code >= 400:
        return {
            "ok": False,
            "status": response.status_code,
            "error": _extract_error_message(payload, response.status_code),
        }
    # 列表类响应补集合元数据，避免条目截断后 AI 数错总数
    payload = _attach_collection_metadata(payload, response)
    return {"ok": True, "status": response.status_code, "data": payload}


__all__ = ["ApiExecutionError", "execute_operation"]
