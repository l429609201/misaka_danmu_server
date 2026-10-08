"""模型数据边界的纯文本判定；不依赖服务、模型或数据库。"""

import re
from typing import Any

_CONTROL_IDENTIFIERS = re.compile(
    r"rate[_\W]*limit|ratelimit|quota|flow[_\W]*control|flowcontrol|"
    r"流控|限流|配额|调用限额|调用次数|daily[_\W]*call[_\W]*limit|"
    r"daily[_\W]*call[_\W]*count", re.IGNORECASE,
)


def is_forbidden_control_identifier(value: Any) -> bool:
    """识别流控、配额及计数标识，不执行任何运行数据查询。"""
    return isinstance(value, str) and bool(_CONTROL_IDENTIFIERS.search(value))


def contains_forbidden_control_content(data: Any) -> bool:
    """递归检查模型输入输出及源码片段的保护边界。"""
    if isinstance(data, dict):
        return any(contains_forbidden_control_content(key) or contains_forbidden_control_content(value)
                   for key, value in data.items())
    if isinstance(data, (list, tuple)):
        return any(contains_forbidden_control_content(item) for item in data)
    return is_forbidden_control_identifier(data)
