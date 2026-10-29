"""无业务依赖的文本进度条工具。"""


def build_progress_bar(progress: int) -> str:
    """将百分比限制在有效范围并生成固定二十格进度条。"""
    percent = max(0, min(100, int(progress)))
    filled = percent // 5
    return "█" * filled + "░" * (20 - filled)
