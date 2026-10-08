"""
御坂助手 · 安全网关（P2）
------------------------------------------------------------
两大职责：
1. 文件访问安全：白名单目录 + 敏感文件黑名单 + 二进制拦截 + 大小上限 + 路径穿越防护。
   （供"读文件类"工具与后续附件功能调用；只读工具走 DB/Service 的不经过这里。）
2. 工具权限分级：READ_ONLY 直接放行；WRITE 需二次确认；DANGEROUS 一律禁止。

设计原则（KISS/安全优先）：默认拒绝，显式允许。
"""

import os
import re
import logging
from enum import Enum
from typing import Any, List, Optional, Tuple

from src.utils.model_content_policy import (
    contains_forbidden_control_content, is_forbidden_control_identifier,
)

logger = logging.getLogger(__name__)


class ToolPermission(str, Enum):
    """工具权限级别。"""
    READ_ONLY = "read_only"   # 只读：直接执行
    WRITE = "write"           # 写/任务：需用户二次确认
    DANGEROUS = "dangerous"   # 危险：一律禁止暴露


def _normalize(path: str) -> str:
    """规范化为绝对路径（解析 .. 与符号），用于穿越检测。"""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_within_allowed_dirs(target_path: str, allowed_dirs: List[str]) -> bool:
    """校验 target_path 是否位于任一白名单目录内（规范化后前缀匹配，防 ../ 穿越）。"""
    if not allowed_dirs:
        return False
    norm_target = _normalize(target_path)
    for base in allowed_dirs:
        if not base:
            continue
        norm_base = _normalize(base)
        # 用 commonpath 严格判断从属关系，避免 /a/bc 命中 /a/b 前缀误判
        try:
            if os.path.commonpath([norm_target, norm_base]) == norm_base:
                return True
        except ValueError:
            # 不同盘符等无法比较，视为不在白名单
            continue
    return False


def check_file_readable(
    target_path: str, allowed_dirs: List[str]
) -> Tuple[bool, Optional[str]]:
    """
    AI 暂不提供任意文件读取；内容级过滤无法保证流控与凭据不被绕过。
    """
    return False, "AI 任意文件读取已禁用"


# ── 数据出口脱敏 ──────────────────────────────────────────
# 工具返回给 AI 的数据中，凡键名命中敏感字段判定，一律脱敏为 ***，
# 作为"密钥绝不出库门"的最后一道防线（即便未来某工具误取了敏感字段）。
# 判定思路参考 MoviePilot v3 app/agent/policy/secrets.py：
#   先把驼峰/连字符统一规范化为 snake_case，再做「精确字段名 + 后缀」双重匹配，
#   避免子串误伤（如 tokenCount）也避免驼峰漏判（如 aiApiKey/jwtSecretKey）。
_SECRET_FIELD_NAMES = frozenset({
    "access_token", "api_key", "apikey", "api_token", "auth_header",
    "authorization", "client_secret", "cookie", "passkey", "passwd",
    "password", "private_key", "pwd", "refresh_token", "secret",
    "secret_access_key", "secret_key", "token", "jwt", "signature",
    "session_token", "credential", "credentials",
})
# 后缀匹配：xxx_token / xxx_secret / xxx_key / xxx_password 等
# 覆盖元数据源与弹幕源的驼峰命名密钥（tmdbApiKey / tvdbApiKey / gamerCookie / bilibiliCookie …）
_SECRET_FIELD_ENDINGS = tuple(
    f"_{n}" for n in ("token", "secret", "key", "password", "passwd",
                      "credential", "credentials", "apikey", "authorization",
                      "cookie", "auth", "session", "signature")
)

_MAX_FIELD_NAME_CHARS = 256
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _normalize_field_name(value: Any) -> str:
    """将字段名规范化为 snake_case（处理驼峰/缩写/连字符边界）。"""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > _MAX_FIELD_NAME_CHARS:
        text = text[-_MAX_FIELD_NAME_CHARS:]
    text = _ACRONYM_BOUNDARY.sub("_", text)
    text = _CAMEL_BOUNDARY.sub("_", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _is_secret_key(key: Any) -> bool:
    """精确字段名 + 后缀匹配，判定是否为密钥/凭据字段。"""
    normalized = _normalize_field_name(key)
    if not normalized:
        return False
    return (
        normalized in _SECRET_FIELD_NAMES
        or normalized.endswith(_SECRET_FIELD_ENDINGS)
    )


def sanitize_output(data: Any) -> Any:
    """递归脱敏工具返回值：命中敏感键名的值替换为 ***。防止密钥回灌给 AI。"""
    if isinstance(data, dict):
        if any(is_forbidden_control_identifier(k) for k in data):
            return {"error": "流控与配额信息禁止 AI 访问"}
        if any(is_forbidden_control_identifier(data.get(k))
               for k in ("key", "configKey", "operationId", "name")):
            return {"error": "流控与配额信息禁止 AI 访问"}
        return {
            k: ("***" if _is_secret_key(k) and v not in (None, "", 0)
                else sanitize_output(v))
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [sanitize_output(x) for x in data]
    if is_forbidden_control_identifier(data):
        return "***"
    return data


def can_execute(permission: ToolPermission) -> Tuple[bool, bool]:
    """
    根据权限级别返回 (是否可执行, 是否需二次确认)。
    - READ_ONLY: (True, False) 直接执行
    - WRITE:     (True, True)  可执行但需用户确认
    - DANGEROUS: (False, False) 禁止
    """
    if permission == ToolPermission.READ_ONLY:
        return True, False
    if permission == ToolPermission.WRITE:
        return True, True
    return False, False
