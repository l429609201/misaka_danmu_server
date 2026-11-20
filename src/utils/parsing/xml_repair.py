"""弹幕 XML 文件修复工具。

提供 `repair_danmaku_xml` 函数：在标准解析失败后进行宽容修复，
并通过 logger.warning 记录每次修复的策略和影响范围，不静默丢弃信息。

设计原则：
- 仅修复可以安全还原语义的问题（非法控制字符、未转义的 &）；
- 不猜测损坏的结构（标签不匹配、属性缺失），此类情况直接返回 None；
- 修复后必须能通过 ElementTree.fromstring 二次验证，否则不认为修复成功。
"""

import logging
import re
from typing import Optional
from xml.etree import ElementTree

from src.utils.misc.common import clean_xml_string

logger = logging.getLogger(__name__)

# 未转义的 & ——后面没有跟合法的命名实体、十进制或十六进制数字引用
_UNESCAPED_AMP = re.compile(r'&(?!(?:amp|lt|gt|apos|quot|#\d+|#x[0-9a-fA-F]+);)')


def _fix_unescaped_ampersand(xml: str) -> tuple[str, int]:
    """将内容里所有未转义的 & 替换为 &amp;，返回修复后的字符串和替换次数。"""
    result, count = _UNESCAPED_AMP.subn('&amp;', xml)
    return result, count


def repair_danmaku_xml(
    content: str,
    episode_id: int,
    *,
    file_path: str = "",
) -> Optional[str]:
    """尝试修复损坏的弹幕 XML 字符串。

    策略（按顺序级联尝试，前一步成功则不继续）：
    1. 清理 XML 1.0 禁止的控制字符；
    2. 修复未转义的 & 字符；
    3. 同时应用步骤 1 + 2。

    每个策略修复后都用 ElementTree.fromstring 验证，通过才认为成功。

    Args:
        content:    原始 XML 字符串（已读取，解析失败）。
        episode_id: 分集 ID，仅用于日志定位。
        file_path:  文件路径，仅用于日志定位。

    Returns:
        修复成功返回修复后的字符串；所有策略均失败返回 None。
    """
    loc = f"分集 {episode_id}"
    if file_path:
        loc += f"（{file_path}）"

    # ---- 策略 1：清理非法控制字符 ----
    cleaned = clean_xml_string(content)
    if cleaned != content:
        removed = len(content) - len(cleaned)
        try:
            ElementTree.fromstring(cleaned)
            logger.warning(
                "弹幕 XML 修复（%s）：清理了 %d 个非法控制字符，解析通过",
                loc, removed,
            )
            return cleaned
        except ElementTree.ParseError:
            pass

    # ---- 策略 2：修复未转义的 & ----
    fixed_amp, amp_count = _fix_unescaped_ampersand(content)
    if amp_count > 0:
        try:
            ElementTree.fromstring(fixed_amp)
            logger.warning(
                "弹幕 XML 修复（%s）：替换了 %d 处未转义的 &，解析通过",
                loc, amp_count,
            )
            return fixed_amp
        except ElementTree.ParseError:
            pass

    # ---- 策略 3：同时清理控制字符 + 修复 & ----
    if cleaned != content or amp_count > 0:
        combined, combined_amp_count = _fix_unescaped_ampersand(cleaned)
        try:
            ElementTree.fromstring(combined)
            logger.warning(
                "弹幕 XML 修复（%s）：同时清理控制字符（%d 个）+ 替换 &（%d 处），解析通过",
                loc,
                len(content) - len(cleaned),
                combined_amp_count,
            )
            return combined
        except ElementTree.ParseError:
            pass

    logger.error(
        "弹幕 XML 修复失败（%s）：已尝试所有策略（控制字符清理 / & 转义），文件可能存在结构性损坏",
        loc,
    )
    return None
