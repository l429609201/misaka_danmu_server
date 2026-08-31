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

logger = logging.getLogger(__name__)

# 单个文件读取上限（字节）：512KB
MAX_FILE_READ_BYTES = 512 * 1024

# 敏感文件名/后缀黑名单（禁止读取）——命中即拒绝
_SENSITIVE_NAME_KEYWORDS = (
    ".env", "credential", "secret", "token", "password", "passwd",
    "private", "id_rsa", ".pem", ".key", ".pfx", ".p12", ".htpasswd",
)
# 敏感目录片段（路径中出现即拒绝）
_SENSITIVE_DIR_KEYWORDS = (".git", "__pycache__", "node_modules", ".ssh")

# 二进制/非文本扩展名黑名单（禁止读取）
_BINARY_EXTENSIONS = (
    ".db", ".sqlite", ".sqlite3", ".so", ".pyd", ".dll", ".exe", ".bin",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".jpg", ".jpeg", ".png", ".gif",
    ".webp", ".bmp", ".ico", ".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav",
    ".flac", ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".pyc",
)


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


def _looks_binary(sample: bytes) -> bool:
    """内容嗅探：含 NUL 字节视为二进制。"""
    return b"\x00" in sample


def check_file_readable(
    target_path: str, allowed_dirs: List[str]
) -> Tuple[bool, Optional[str]]:
    """
    综合校验文件是否允许读取。返回 (是否允许, 拒绝原因)。
    顺序：存在性 → 白名单 → 敏感名 → 二进制扩展名 → 大小 → 内容嗅探。
    """
    if not target_path:
        return False, "路径为空"

    # 1. 白名单目录（最先，防穿越）
    if not is_within_allowed_dirs(target_path, allowed_dirs):
        return False, "该路径不在允许访问的目录白名单内"

    norm = _normalize(target_path)
    lower = norm.lower()

    # 2. 敏感目录片段
    for kw in _SENSITIVE_DIR_KEYWORDS:
        if kw in lower:
            return False, f"命中敏感目录（{kw}），禁止访问"

    # 3. 敏感文件名/后缀
    base_lower = os.path.basename(lower)
    for kw in _SENSITIVE_NAME_KEYWORDS:
        if kw in base_lower:
            return False, "命中敏感文件（可能含密钥/凭据），禁止读取"

    # 4. 二进制扩展名
    _, ext = os.path.splitext(lower)
    if ext in _BINARY_EXTENSIONS:
        return False, f"二进制文件（{ext}）禁止读取"

    # 5. 存在性与类型
    if not os.path.isfile(norm):
        return False, "文件不存在或不是常规文件"

    # 6. 大小上限
    try:
        if os.path.getsize(norm) > MAX_FILE_READ_BYTES:
            return False, f"文件过大（超过 {MAX_FILE_READ_BYTES // 1024}KB）"
    except OSError as e:
        return False, f"无法获取文件信息：{e}"

    # 7. 内容嗅探（读前 4KB 判断是否二进制）
    try:
        with open(norm, "rb") as f:
            head = f.read(4096)
        if _looks_binary(head):
            return False, "文件内容疑似二进制，禁止读取"
    except OSError as e:
        return False, f"无法读取文件：{e}"

    return True, None


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
        return {
            k: ("***" if _is_secret_key(k) and v not in (None, "", 0)
                else sanitize_output(v))
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [sanitize_output(x) for x in data]
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
