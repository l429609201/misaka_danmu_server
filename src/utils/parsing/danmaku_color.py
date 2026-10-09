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
    "\u2764",        # heart_red
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
        try:
            # 普通数字配置是十进制；只有显式前缀才按十六进制解析。
            base = 16 if v.startswith(("#", "0x")) else 10
            if v.startswith("#"):
                v = v[1:]
            return max(0, min(16777215, int(v, base)))
        except ValueError:
            return 16777215
    return 16777215


def _get_color_from_p(parts: List[str]) -> int:
    """兼容三/四段 API 和八/九段 XML，不把字号当成颜色。"""
    if len(parts) < 3:
        return 16777215
    index = 3 if len(parts) >= 8 else 2
    try:
        return int(parts[index])
    except (ValueError, IndexError):
        return 16777215


def _set_color_in_p(parts: List[str], color: int) -> None:
    """按输入布局写回颜色，保留 XML 字号和元数据。"""
    if len(parts) < 3:
        return
    parts[3 if len(parts) >= 8 else 2] = str(color)


def parse_color_palette(config_value: Any) -> Optional[List[int]]:
    """
    解析颜色配置字符串，返回色板列表。
    支持 JSON 数组 / 逗号分隔 / 空字符串（返回 None）。
    """
    if isinstance(config_value, list):
        return [_normalize_color_value(value) for value in config_value]
    if not config_value or not str(config_value).strip():
        return None
    config_value = str(config_value).strip()
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
            # 点赞处理器追加的是后缀，同时兼容已有前缀样式。
            is_like = any(marker in m_text for marker in _LIKE_EMOJI_PREFIXES) or bool(_LIKE_NUM_ONLY_RE.search(m_text))
            if is_like:
                parts = p_attr.split(",")
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
    """按历史输出规则为达到阈值的 X次数后缀弹幕染色。"""
    palette = palette_list or DEFAULT_RANDOM_COLOR_PALETTE
    processed = []
    for item in comments:
        match = _REPEAT_SUFFIX_RE.match(item.get('m', ''))
        if match and int(match.group(2)) >= min_count and item.get('p'):
            parts = item['p'].split(',')
            _set_color_in_p(parts, random.choice(palette))
            processed.append({**item, 'p': ','.join(parts)})
        else:
            processed.append(item)
    return processed
