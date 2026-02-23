"""任务工具函数模块"""
import logging
import re
from typing import List

# 从统一模块 re-export，保持向后兼容
from src.utils.filename_parser import (
    parse_episode_ranges,
    is_movie_by_title,
    is_chinese_title,
    format_episode_ranges as _format_episode_ranges,
)

logger = logging.getLogger(__name__)


def generate_episode_range_string(episode_indices: List[int]) -> str:
    """向后兼容包装: 使用统一模块的 format_episode_ranges"""
    return _format_episode_ranges(episode_indices, separator=", ")


def extract_short_error_message(error: Exception) -> str:
    """
    从异常对象中提取简短的错误消息，用于任务管理器显示

    Args:
        error: 异常对象

    Returns:
        简短的错误描述（不包含SQL语句、堆栈等详细信息）
    """
    error_str = str(error)

    # 如果是数据库错误，只保留错误类型和简短描述
    if "DataError" in error_str or "IntegrityError" in error_str or "OperationalError" in error_str:
        # 提取错误类型
        error_type = type(error).__name__

        # 尝试提取MySQL/PostgreSQL错误消息（在括号中）
        match = re.search(r'\((\d+),\s*"([^"]+)"\)', error_str)
        if match:
            error_code = match.group(1)
            error_msg = match.group(2)
            return f"{error_type} ({error_code}): {error_msg}"

        # 如果没有匹配到，返回错误类型
        return error_type

    # 对于其他错误，只返回第一行
    first_line = error_str.split('\n')[0]
    # 限制长度
    if len(first_line) > 100:
        return first_line[:97] + "..."
    return first_line
