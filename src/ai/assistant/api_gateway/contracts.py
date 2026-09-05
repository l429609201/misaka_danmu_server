"""
御坂助手 · API 网关风险契约
------------------------------------------------------------
对齐 MoviePilot v3 `app/agent/policy/contracts.py` 的正交风险描述思路：
用「副作用类别 / 确认强度 / 结果敏感度」三个独立维度描述一个操作，
而不是压成单一的权限档位。

为什么需要三个维度而非一个权限枚举：
- `token.update` 与 `token.delete` 都是写操作，但前者可逆、后者不可逆，
  需要的确认强度完全不同。只有 READ_ONLY/WRITE 两档时无法区分。
- `token.list` 与 `token.create` 都要返回 token 字段，但前者是读既有密钥
  （必须打码），后者是交付本轮新生成的凭据（需明文给用户）。

未引入 MP 的 origin 四维准入（interactive/machine/background/subagent_allowed）
与 migration_state：本项目助手只有 Web 单一入口，也没有渐进迁移的历史包袱，
引入这些分支属于 YAGNI。
"""

from __future__ import annotations

from enum import Enum

from ..security_gateway import ToolPermission


class ActionEffect(str, Enum):
    """操作的实际副作用类别。"""

    SAFE_READ = "safe_read"                    # 纯读，无副作用
    SENSITIVE_READ = "sensitive_read"          # 读，但结果含凭据/隐私
    REVERSIBLE_WRITE = "reversible_write"      # 写，可通过再次操作还原
    DESTRUCTIVE_WRITE = "destructive_write"    # 写，不可逆（删除、清空）
    EXTERNAL_SIDE_EFFECT = "external_side_effect"  # 触发外部动作（下载、推送）


class ConfirmationMode(str, Enum):
    """执行前所需的用户确认强度。"""

    NONE = "none"          # 无需确认，可直接执行
    REQUIRED = "required"  # 必须先向用户说明并获得同意


class ResultSensitivity(str, Enum):
    """返回结果进入对话上下文时的敏感等级。"""

    NORMAL = "normal"    # 普通业务数据
    PRIVATE = "private"  # 含个人可识别信息，不宜复述
    SECRET = "secret"    # 含凭据；除显式豁免字段外一律打码


# 副作用类别 → 默认确认强度。
# 只有纯读可免确认；任何写入或外部动作都必须先获得用户同意。
_DEFAULT_CONFIRMATION: dict[ActionEffect, ConfirmationMode] = {
    ActionEffect.SAFE_READ: ConfirmationMode.NONE,
    ActionEffect.SENSITIVE_READ: ConfirmationMode.REQUIRED,
    ActionEffect.REVERSIBLE_WRITE: ConfirmationMode.REQUIRED,
    ActionEffect.DESTRUCTIVE_WRITE: ConfirmationMode.REQUIRED,
    ActionEffect.EXTERNAL_SIDE_EFFECT: ConfirmationMode.REQUIRED,
}

# 副作用类别 → 既有三档权限（用于复用 ToolRegistry 的权限校验与工具导出过滤）
_EFFECT_TO_PERMISSION: dict[ActionEffect, ToolPermission] = {
    ActionEffect.SAFE_READ: ToolPermission.READ_ONLY,
    ActionEffect.SENSITIVE_READ: ToolPermission.READ_ONLY,
    ActionEffect.REVERSIBLE_WRITE: ToolPermission.WRITE,
    ActionEffect.DESTRUCTIVE_WRITE: ToolPermission.WRITE,
    ActionEffect.EXTERNAL_SIDE_EFFECT: ToolPermission.WRITE,
}

# 面向用户的中文说明，进入 list_api_operations 的返回供 AI 判断措辞轻重
EFFECT_LABELS: dict[ActionEffect, str] = {
    ActionEffect.SAFE_READ: "只读查询",
    ActionEffect.SENSITIVE_READ: "读取敏感信息",
    ActionEffect.REVERSIBLE_WRITE: "可逆修改",
    ActionEffect.DESTRUCTIVE_WRITE: "不可逆操作",
    ActionEffect.EXTERNAL_SIDE_EFFECT: "触发外部动作",
}


def default_confirmation(effect: ActionEffect) -> ConfirmationMode:
    """
    取该副作用类别的默认确认强度。

    :param effect: 副作用类别
    :return: 确认强度；未知类别按最严格处理
    """
    return _DEFAULT_CONFIRMATION.get(effect, ConfirmationMode.REQUIRED)


def effect_to_permission(effect: ActionEffect) -> ToolPermission:
    """
    把副作用类别映射到既有三档权限，供 ToolRegistry 复用。

    :param effect: 副作用类别
    :return: 工具权限档位；未知类别按写操作处理
    """
    return _EFFECT_TO_PERMISSION.get(effect, ToolPermission.WRITE)


def is_destructive(effect: ActionEffect) -> bool:
    """
    判断是否为不可逆操作（用于在提示里加重警示措辞）。

    :param effect: 副作用类别
    :return: 是否不可逆
    """
    return effect is ActionEffect.DESTRUCTIVE_WRITE


__all__ = [
    "ActionEffect",
    "ConfirmationMode",
    "ResultSensitivity",
    "EFFECT_LABELS",
    "default_confirmation",
    "effect_to_permission",
    "is_destructive",
]
