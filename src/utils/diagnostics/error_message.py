"""异常展示辅助：保持纯字符串处理，不依赖任务或服务。"""
import re


def extract_short_error_message(error: Exception) -> str:
    """提取适合进度展示的简短错误，避免展示完整 SQL 和堆栈。"""
    error_str = str(error)
    if any(name in error_str for name in ("DataError", "IntegrityError", "OperationalError")):
        error_type = type(error).__name__
        match = re.search(r'\((\d+),\s*"([^"]+)"\)', error_str)
        if match:
            return f"{error_type} ({match.group(1)}): {match.group(2)}"
        return error_type
    first_line = error_str.split('\n')[0]
    return first_line[:97] + "..." if len(first_line) > 100 else first_line
