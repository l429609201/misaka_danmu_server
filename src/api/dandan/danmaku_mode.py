"""弹幕位置类型（mode）转换工具。

dandanplay 弹幕 p 字段格式为 "时间,模式,颜色,用户ID"，其中模式(mode)位于索引 1：
- 1 = 滚动（浮动）
- 4 = 底部
- 5 = 顶部
- 6 = 逆向
- 7 = 高级
- 8 = 代码

设计要点：
1. 仅在「输出时」转换，不改动已存储的弹幕数据，可随时通过配置关闭。
2. 基于「原始 mode」一次性映射：先判断每条弹幕的原始类型再转换，
   转换后的弹幕不会被二次转换（避免 顶→底→又被底→顶 的连锁横跳）。
"""
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# mode 值常量
_SCROLL_MODE = "1"
_BOTTOM_MODE = "4"
_TOP_MODE = "5"

# 顶部弹幕允许的转换目标 → 对应 mode 值
_TOP_TARGET_MAP = {"bottom": _BOTTOM_MODE, "scroll": _SCROLL_MODE}
# 底部弹幕允许的转换目标 → 对应 mode 值
_BOTTOM_TARGET_MAP = {"top": _TOP_MODE, "scroll": _SCROLL_MODE}


def convert_danmaku_position(
    comments: List[Dict[str, Any]],
    top_to: str = "none",
    bottom_to: str = "none",
) -> List[Dict[str, Any]]:
    """按配置转换顶部/底部弹幕的位置类型（基于原始 mode 一次性映射）。

    :param comments: 弹幕列表，每项含 p 字段（"时间,模式,颜色,..."）
    :param top_to: 顶部弹幕(5)转换目标：none(不转)/bottom/scroll
    :param bottom_to: 底部弹幕(4)转换目标：none(不转)/top/scroll
    :return: 转换后的弹幕列表（仅改动命中项的 p 字段，其余原样返回）
    """
    top_target = _TOP_TARGET_MAP.get(top_to)
    bottom_target = _BOTTOM_TARGET_MAP.get(bottom_to)
    # 两个方向都不需要转换则直接返回
    if not comments or (top_target is None and bottom_target is None):
        return comments

    converted = 0
    processed: List[Dict[str, Any]] = []
    for item in comments:
        p_attr = item.get("p", "")
        if not p_attr:
            processed.append(item)
            continue

        parts = p_attr.split(",")
        if len(parts) < 2:
            processed.append(item)
            continue

        # 基于原始 mode 判断，命中后只映射一次（转换后的结果不会再被转换）
        original_mode = parts[1]
        new_mode = None
        if original_mode == _TOP_MODE and top_target is not None:
            new_mode = top_target
        elif original_mode == _BOTTOM_MODE and bottom_target is not None:
            new_mode = bottom_target

        if new_mode is not None and new_mode != original_mode:
            parts[1] = new_mode
            processed.append({**item, "p": ",".join(parts)})
            converted += 1
        else:
            processed.append(item)

    if converted:
        logger.debug(
            f"弹幕位置转换：top_to={top_to}, bottom_to={bottom_to}，共转换 {converted} 条"
        )
    return processed
