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
# - highlight_only: 仅对点赞弹幕（含 🤍/🔥）上色，重复弹幕由 apply_repeat_highlight 统一处理
DEFAULT_RANDOM_COLOR_MODE = "off"
VALID_RANDOM_COLOR_MODES = {"off", "white_to_random", "all_random", "all_white", "highlight_only"}

# 点赞弹幕的常见前缀（白心、黑心、火焰、括号点赞、括号热门等）
_LIKE_EMOJI_PREFIXES = (
    "\U0001f90d",      # white_heart   🤍 (U+1F90D)
    "\u2661",          # heart_outline ♡ (U+2661)
    "\U0001f525",      # fire          🔥
    "[\U0001f44d",     # like_bracket  [👍
    "(\u70b9\u8d5e",   # text 普通    (点赞
    "(\u70ed\u95e8",   # text 热门    (热门
)
_LIKE_NUM_ONLY_RE = re.compile(r'\s\+\d+(?:\.\d+)?[wk]?$')

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
        if v.startswith("#"):
            v = v[1:]
        if v.startswith("0x"):
            v = v[2:]
        try:
            return max(0, min(16777215, int(v, 16)))
        except ValueError:
            return 16777215
    return 16777215


def _get_color_from_p(parts: List[str]) -> int:
    """从 p 字段的 parts 中提取颜色值（索引 2）。"""
    if len(parts) < 3:
        return 16777215
    try:
        return int(parts[2])
    except (ValueError, IndexError):
        return 16777215


def _set_color_in_p(parts: List[str], color: int) -> None:
    """修改 parts 列表中的颜色值（索引 2）。"""
    if len(parts) < 3:
        return
    parts[2] = str(color)


def parse_color_palette(config_value: str) -> Optional[List[int]]:
    """
    解析颜色配置字符串，返回色板列表。
    支持 JSON 数组 / 逗号分隔 / 空字符串（返回 None）。
    """
    if not config_value or not config_value.strip():
        return None
    config_value = config_value.strip()
    if config_value.startswith("["):
        try:
            arr = json.loads(config_value)
            if isinstance(arr, list):
                return [_normalize_color_value(v) for v in arr]
        except json.JSONDecodeError:
            pass
    parts = [p.strip() for p in config_value.split(",") if p.strip()]
    if parts:
        return [_normalize_color_value(p) for p in parts]
    return None


def apply_random_color(
    comments: Iterable[Dict[str, Any]],
    mode: str = "off",
    palette_list: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """
    对弹幕应用随机色功能。

    Args:
        comments: 弹幕列表，每项含 'p' 和 'm' 字段
        mode: 随机色模式
            - "off": 不改色
            - "white_to_random": 仅白色弹幕随机换色
            - "all_random": 所有弹幕随机换色
            - "all_white": 所有弹幕变白色
            - "highlight_only": 仅对点赞弹幕上色（白心、火焰等前缀）
        palette_list: 色板（十进制颜色值列表），为空则使用默认色板

    Returns:
        处理后的弹幕列表（若不需改色则原样返回）
    """
    if mode not in VALID_RANDOM_COLOR_MODES:
        logger.warning(f"无效的随机色模式 '{mode}'，将使用 'off'")
        mode = "off"

    if mode == "off":
        return list(comments)

    if palette_list is None or not palette_list:
        palette_list = DEFAULT_RANDOM_COLOR_PALETTE

    # all_white 模式：所有弹幕统一变白色
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
                processed.append({**item, "p": ",".join(parts)})
            else:
                processed.append(item)
        return processed

    # highlight_only 模式：仅对点赞弹幕上色
    if mode == "highlight_only":
        processed = []
        for item in comments:
            p_attr = item.get("p", "")
            m_text = item.get("m", "")
            if not p_attr or not m_text:
                processed.append(item)
                continue

            # 检查是否以点赞前缀开头
            is_like = m_text.startswith(_LIKE_EMOJI_PREFIXES)
            if is_like:
                parts = p_attr.split(",")
                current_color = _get_color_from_p(parts)
                if current_color == 16777215:  # 只对白色点赞弹幕上色
                    new_color = random.choice(palette_list)
                    _set_color_in_p(parts, new_color)
                    processed.append({**item, "p": ",".join(parts)})
                    continue
            processed.append(item)
        return processed

    # white_to_random / all_random 模式
    if mode not in ("white_to_random", "all_random"):
        return list(comments)

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


def apply_repeat_highlight(
    comments: Iterable[Dict[str, Any]],
    min_count: int = DEFAULT_REPEAT_HIGHLIGHT_MIN_COUNT,
    palette_list: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """
    对重复弹幕进行高亮处理（随机上色）。

    识别逻辑：
    1. 内容完全相同的弹幕，统计出现次数
    2. 或者匹配 "内容 X数字" 格式的弹幕（如 "好好好 X50"）

    Args:
        comments: 弹幕列表
        min_count: 最小重复次数阈值（默认3）
        palette_list: 色板列表，为空则使用默认

    Returns:
        处理后的弹幕列表
    """
    if min_count < 2:
        return list(comments)

    if palette_list is None or not palette_list:
        palette_list = DEFAULT_RANDOM_COLOR_PALETTE

    # 第一遍：统计每个内容的出现次数
    content_counts: Dict[str, int] = {}
    for item in comments:
        m_text = item.get("m", "").strip()
        if not m_text:
            continue

        # 尝试匹配 "内容 X数字" 格式
        match = _REPEAT_SUFFIX_RE.match(m_text)
        if match:
            base_content = match.group(1).strip()
            repeat_num = int(match.group(2))
            # 如果后缀的数字 >= min_count，则标记该基础内容
            if repeat_num >= min_count:
                content_counts[base_content] = max(content_counts.get(base_content, 0), repeat_num)
        else:
            # 普通弹幕：统计次数
            content_counts[m_text] = content_counts.get(m_text, 0) + 1

    # 第二遍：对达到阈值的内容随机上色
    processed = []
    for item in comments:
        p_attr = item.get("p", "")
        m_text = item.get("m", "").strip()
        if not p_attr or not m_text:
            processed.append(item)
            continue

        # 判断该弹幕是否需要高亮
        should_highlight = False
        match = _REPEAT_SUFFIX_RE.match(m_text)
        if match:
            base_content = match.group(1).strip()
            if content_counts.get(base_content, 0) >= min_count:
                should_highlight = True
        elif content_counts.get(m_text, 0) >= min_count:
            should_highlight = True

        if should_highlight:
            parts = p_attr.split(",")
            current_color = _get_color_from_p(parts)
            # 只对白色弹幕上色
            if current_color == 16777215:
                new_color = random.choice(palette_list)
                _set_color_in_p(parts, new_color)
                processed.append({**item, "p": ",".join(parts)})
                continue

        processed.append(item)

    return processed
