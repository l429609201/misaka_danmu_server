"""
正则分支拆分工具

把一条由 `|` 组合的正则拆成可单独匹配的分支，用于在日志中报告“具体命中了哪条规则”。
典型场景：分集黑名单正则命中时，只打印命中的那一个分支，而不是整条长正则。
"""

import re
from typing import List, Optional, Tuple

__all__ = ["split_regex_alternatives", "find_matching_alternative"]

# 分组前缀：(?:  (?=  (?!  (?<=  (?<!  (?P<name>  (?<name>
_GROUP_PREFIX_RE = re.compile(r"\?(?:P<[^>]+>|[:=!]|<[=!]|<[^>]+>)?")


def _scan_regex_structure(src: str) -> Tuple[List[int], List[Tuple[int, int]]]:
    """扫描正则源码，返回（顶层未转义 `|` 的位置, 每个分组的 (左括号, 右括号) 位置）。

    跳过转义字符与字符类 `[...]` 内部；只用于日志展示，不追求覆盖全部正则语法。
    """
    bars: List[int] = []
    groups: List[Tuple[int, int]] = []
    stack: List[int] = []
    in_class, escaped = False, False
    for i, ch in enumerate(src):
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif in_class:
            in_class = ch != "]"  # 字符类内只关心未转义的闭合 ]
        elif ch == "[":
            in_class = True
        elif ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            groups.append((stack.pop(), i))
        elif ch == "|" and not stack:
            bars.append(i)
    groups.sort()
    return bars, groups


def _split_at(src: str, cuts: List[int]) -> List[str]:
    """按给定位置切分字符串（切点字符本身丢弃）。"""
    parts, start = [], 0
    for c in cuts:
        parts.append(src[start:c])
        start = c + 1
    parts.append(src[start:])
    return parts


def split_regex_alternatives(pattern: "re.Pattern") -> List[Tuple[str, "re.Pattern"]]:
    """把正则拆成 (分支展示文本, 可单独匹配的正则) 列表。

    - 顶层有 `|`（如 `预告|花絮|^(.*?)重制版$`）：按顶层分支拆分，展示分支本身；
    - 顶层无 `|` 但被包了一层（如 `^(.*?)(预告|花絮|.+?版)(.*?)$`）：找到第一个含分支的分组，
      把每个分支放回原位置后单独编译，展示该分支；
    任一分支无法单独编译（如跨分支反向引用）时退化为 [(原正则文本, 原正则)]，保证不会误报。
    """
    src = pattern.pattern
    bars, groups = _scan_regex_structure(src)
    try:
        if bars:
            return [(p, re.compile(p, pattern.flags)) for p in _split_at(src, bars) if p]
        for open_i, close_i in groups:
            inner_start = open_i + 1
            # 跳过分组前缀，只拆分组内容
            prefix = _GROUP_PREFIX_RE.match(src, inner_start, close_i)
            if prefix and prefix.group(0):
                inner_start = prefix.end()
            inner = src[inner_start:close_i]
            inner_bars, _ = _scan_regex_structure(inner)
            if not inner_bars:
                continue
            head, tail = src[:inner_start], src[close_i:]
            return [
                (branch, re.compile(head + branch + tail, pattern.flags))
                for branch in _split_at(inner, inner_bars) if branch
            ]
    except re.error:
        pass
    return [(src, pattern)]


def find_matching_alternative(
    text: str,
    alternatives: List[Tuple[str, "re.Pattern"]],
    default: Optional[str] = None,
) -> Optional[str]:
    """返回第一个能匹配 text 的分支展示文本；都不匹配时返回 default。"""
    return next((label for label, alt in alternatives if alt.search(text)), default)
