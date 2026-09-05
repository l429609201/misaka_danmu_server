"""御坂助手 · API 网关（白名单 operation → 内部 ASGI 调用）"""

from .contracts import (
    ActionEffect,
    ConfirmationMode,
    EFFECT_LABELS,
    ResultSensitivity,
    default_confirmation,
    effect_to_permission,
    is_destructive,
)
from .policy import (
    ApiOperation,
    EXPOSED_OPERATIONS,
    FORBIDDEN_PATH_PREFIXES,
    list_exposed_operations,
    resolve_api_operation,
)
from .registry import (
    RouteFact,
    list_coverage_gap,
    reflect_routes,
    validate_whitelist,
)
from .executor import ApiExecutionError, execute_operation

__all__ = [
    "ActionEffect",
    "ConfirmationMode",
    "ResultSensitivity",
    "EFFECT_LABELS",
    "default_confirmation",
    "effect_to_permission",
    "is_destructive",
    "ApiOperation",
    "EXPOSED_OPERATIONS",
    "FORBIDDEN_PATH_PREFIXES",
    "list_exposed_operations",
    "resolve_api_operation",
    "RouteFact",
    "list_coverage_gap",
    "reflect_routes",
    "validate_whitelist",
    "ApiExecutionError",
    "execute_operation",
]
