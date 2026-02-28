import json
import logging
import random
import re
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# 默认随机色板
DEFAULT_RANDOM_COLOR_PALETTE: List[int] = [
    16777215, 16777215, 16777215, 16777215, 16777215, 16777215, 16777215, 16777215,
    16744319, 16752762, 16774799, 9498256, 8388564, 8900346, 14204888, 16758465,
]

# 颜色模式：
# - off: 不改色
# - white_to_random: 仅将白色弹幕随机换色
# - all_random: 所有弹幕随机换色
# - all_white: 所有弹幕变白色
DEFAULT_RANDOM_COLOR_MODE = "off"
VALID_RANDOM_COLOR_MODES = {"off", "white_to_random", "all_random", "all_white"}

# 重复弹幕高亮：最小重复次数为 3（颜色从随机色板中随机取，与普通弹幕行为一致）
DEFAULT_REPEAT_HIGHLIGHT_MIN_COUNT: int = 3

# 匹配 "内容 X数字" 结尾的弹幕（如 "好好好 X50"）
_REPEAT_SUFFIX_RE = re.compile(r'^(.*?)\s+X(\d+)$')


def _normalize_color_value(value: Any) -> int:
    """将颜色值规范为 int（支持 #RRGGBB / 0x / 纯数字字符串）。"""
    if isinstance(value, int):
        return max(0, min(16777215, value))
    if isinstance(value, str):
        v = value.strip().lower()

        # 快速检查：如果包含明显的非颜色字符（如 [Gamer]xxx、用户名等），直接返回白色
        # 这些值通常是平台错误地将其他数据放在了颜色字段中
        if any(char in v for char in ['[', ']', ' ', '@', '_']) or v.startswith('gamer'):
            return 16777215

        try:
            if v.startswith("#"):
                # 十六进制颜色格式 #RRGGBB
                hex_str = v[1:]
                if len(hex_str) == 6 and all(c in '0123456789abcdef' for c in hex_str):
                    return int(hex_str, 16)
                # 格式错误但看起来像是尝试写十六进制
                logger.debug("颜色格式错误（期望 #RRGGBB）: %s", value)
                return 16777215
            if v.startswith("0x"):
                # 0x前缀的十六进制
                return int(v, 16)
            # 纯数字字符串
            color_int = int(v)
            return max(0, min(16777215, color_int))
        except ValueError:
            # 只对看起来像颜色值但解析失败的情况记录调试日志
            if v.isdigit() or v.startswith('#') or v.startswith('0x'):
                logger.debug("无法解析颜色值: %s", value)
            return 16777215
    return 16777215


def parse_palette(raw_value: Any) -> List[int]:
    """
    将配置中的色板解析为 int 列表。
    支持 JSON 数组、逗号分隔字符串、直接传入列表。
    """
    if raw_value is None:
        return DEFAULT_RANDOM_COLOR_PALETTE

    if isinstance(raw_value, list):
        values = raw_value
    else:
        raw_str = str(raw_value).strip()
        if not raw_str:
            return DEFAULT_RANDOM_COLOR_PALETTE
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, list):
                values = parsed
            else:
                values = []
        except json.JSONDecodeError:
            values = raw_str.split(",")

    palette = [_normalize_color_value(v) for v in values if str(v).strip()]
    return palette or DEFAULT_RANDOM_COLOR_PALETTE


def _get_color_from_p(parts: List[str]) -> int:
    """从弹幕 p 属性数组中获取颜色值。"""
    if len(parts) >= 4:
        return _normalize_color_value(parts[3])
    if len(parts) >= 3:
        return _normalize_color_value(parts[2])
    return 16777215


def _set_color_in_p(parts: List[str], color: int) -> None:
    """在 p 属性数组中写回颜色值。"""
    color_str = str(color)
    if len(parts) >= 4:
        parts[3] = color_str
    elif len(parts) >= 3:
        parts[2] = color_str
    else:
        # 兜底：不足 3 个字段时填充
        while len(parts) < 3:
            parts.append("0")
        parts.append(color_str)


def apply_repeat_highlight(
    comments: List[Dict[str, Any]],
    min_count: int = DEFAULT_REPEAT_HIGHLIGHT_MIN_COUNT,
    palette: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """
    对重复次数达到阈值的弹幕从色板中随机取色，行为与普通随机弹幕一致。

    检测规则：弹幕内容（m 字段）以 " X数字" 结尾，如 "好好好 X50"。
    当数字 >= min_count 时，从 palette（默认随机色板）随机选一个颜色替换。
    """
    use_palette = palette if palette else DEFAULT_RANDOM_COLOR_PALETTE

    processed = []
    for item in comments:
        m = item.get("m", "")
        match = _REPEAT_SUFFIX_RE.match(m)
        if match:
            try:
                count = int(match.group(2))
            except ValueError:
                count = 0
            if count >= min_count:
                p_attr = item.get("p", "")
                if p_attr:
                    parts = p_attr.split(",")
                    _set_color_in_p(parts, random.choice(use_palette))
                    processed.append({**item, "p": ",".join(parts)})
                    continue
        processed.append(item)
    return processed


def apply_random_color(
    comments: List[Dict[str, Any]],
    mode: str,
    palette: Iterable[int],
) -> List[Dict[str, Any]]:
    """
    根据模式对弹幕颜色进行转换。
    - off: 不处理
    - white_to_random: 仅将白色弹幕随机换色
    - all_random: 所有弹幕随机换色
    - all_white: 所有弹幕变白色
    """
    if mode not in VALID_RANDOM_COLOR_MODES or mode == "off":
        return comments

    # all_white 模式：将所有弹幕颜色设为白色
    if mode == "all_white":
        processed = []
        for item in comments:
            p_attr = item.get("p", "")
            if not p_attr:
                processed.append(item)
                continue

            parts = p_attr.split(",")
            current_color = _get_color_from_p(parts)

            if current_color != 16777215:
                _set_color_in_p(parts, 16777215)
                new_p = ",".join(parts)
                processed.append({**item, "p": new_p})
            else:
                processed.append(item)
        return processed

    # 随机颜色模式
    palette_list = list(palette)
    if not palette_list:
        palette_list = DEFAULT_RANDOM_COLOR_PALETTE

    processed = []
    for item in comments:
        p_attr = item.get("p", "")
        if not p_attr:
            processed.append(item)
            continue

        parts = p_attr.split(",")
        current_color = _get_color_from_p(parts)

        should_replace = (
            mode == "all_random"
            or (mode == "white_to_random" and current_color == 16777215)
        )

        if should_replace:
            new_color = random.choice(palette_list)
            if new_color != current_color:
                _set_color_in_p(parts, new_color)
                new_p = ",".join(parts)
                processed.append({**item, "p": new_p})
                continue

        processed.append(item)

    return processed
