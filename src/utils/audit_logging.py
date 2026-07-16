"""审计日志请求信息安全处理工具。"""

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode
from xml.etree import ElementTree

from fastapi import Request

_MAX_REQUEST_BODY_BYTES = 16 * 1024
_SENSITIVE_KEYS = {
    "authorization", "cookie", "setcookie", "password", "passwd", "pwd",
    "token", "accesstoken", "refreshtoken", "apikey", "secret",
    "clientsecret", "credential", "session", "privatekey",
}


def _is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in _SENSITIVE_KEYS or any(
        normalized.endswith(item) for item in ("password", "token", "secret", "apikey")
    )


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if _is_sensitive_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def build_audit_request_headers(request: Request) -> str:
    """过滤敏感请求头和查询参数后生成审计字段。"""
    headers = {
        key: value for key, value in request.headers.items()
        if not _is_sensitive_key(key)
    }
    if request.url.query:
        query_items = [
            (key, "***" if _is_sensitive_key(key) else value)
            for key, value in parse_qsl(request.url.query, keep_blank_values=True)
        ]
        headers["_query"] = urlencode(query_items)
    return json.dumps(headers, ensure_ascii=False, indent=2)


def _metadata(content_type: str, length: int | None, reason: str, digest: str | None = None) -> str:
    data = {
        "记录方式": "仅元数据",
        "contentType": content_type or "unknown",
        "length": length,
        "reason": reason,
    }
    if digest:
        data["sha256"] = digest
    return json.dumps(data, ensure_ascii=False, indent=2)


def _format_xml(raw_body: bytes) -> str:
    root = ElementTree.fromstring(raw_body)
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if _is_sensitive_key(tag):
            element.text = "***"
            element[:] = []
        for key in list(element.attrib):
            if _is_sensitive_key(key):
                element.attrib[key] = "***"
    return ElementTree.tostring(root, encoding="unicode")


def format_audit_request_body(raw_body: bytes, content_type: str) -> str | None:
    """按内容类型格式化并脱敏；二进制只记录摘要。"""
    if not raw_body:
        return None
    media_type = content_type.split(";", 1)[0].strip().lower()
    digest = hashlib.sha256(raw_body).hexdigest()
    try:
        if media_type == "application/json" or media_type.endswith("+json"):
            return json.dumps(_redact(json.loads(raw_body)), ensure_ascii=False, indent=2)
        if media_type in ("application/x-www-form-urlencoded",):
            items = [
                (key, "***" if _is_sensitive_key(key) else value)
                for key, value in parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True)
            ]
            return urlencode(items)
        if media_type in ("application/xml", "text/xml") or media_type.endswith("+xml"):
            return _format_xml(raw_body)
        if media_type.startswith("text/"):
            text = raw_body.decode("utf-8")
            pattern = r"(?i)(password|passwd|token|api[_-]?key|secret)(\s*[:=]\s*)([^\s,;&]+)"
            return re.sub(pattern, lambda match: f"{match.group(1)}{match.group(2)}***", text)
    except (UnicodeDecodeError, json.JSONDecodeError, ElementTree.ParseError, ValueError):
        return _metadata(content_type, len(raw_body), "内容解析失败，未记录原文", digest)
    return _metadata(content_type, len(raw_body), "二进制内容不落盘", digest)


async def capture_audit_request_body(request: Request) -> str | None:
    """仅读取可确认不超过 16 KiB 的请求体，避免审计逻辑放大内存占用。"""
    content_type = request.headers.get("content-type", "")
    length_header = request.headers.get("content-length")
    try:
        content_length = int(length_header) if length_header is not None else None
    except ValueError:
        content_length = None
    if content_length is None:
        return _metadata(content_type, None, "缺少有效 Content-Length，未读取请求体")
    if content_length > _MAX_REQUEST_BODY_BYTES:
        return _metadata(content_type, content_length, "请求体超过 16 KiB，未读取原文")
    raw_body = await request.body()
    if len(raw_body) > _MAX_REQUEST_BODY_BYTES:
        return _metadata(content_type, len(raw_body), "实际请求体超过 16 KiB，未记录原文")
    return format_audit_request_body(raw_body, content_type)
