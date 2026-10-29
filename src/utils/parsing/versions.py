"""版本字符串的纯格式判定。"""

import re
from typing import Optional


_SEMVER_LIKE_PATTERN = re.compile(r"^v?\d+(\.\d+)+([.\-+].*)?$")


def is_semantic_version(value: Optional[str]) -> bool:
    """区分语义版本与 test、nightly 等固定发布标签。"""
    # 发布标签不得被当成包版本写回权威清单。
    return bool(value) and bool(_SEMVER_LIKE_PATTERN.match(str(value).strip()))
