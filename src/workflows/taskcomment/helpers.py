"""
任务弹幕轮询辅助函数
"""

from typing import Optional


def parse_episode_id_from_unique_key(unique_key: Optional[str]) -> Optional[int]:
    """
    从 uniqueKey 解析 episodeId

    Args:
        unique_key: 任务的唯一标识键

    Returns:
        解析出的 episodeId，如果无法解析则返回 None
    """
    if not unique_key:
        return None

    for prefix in ("match_fallback_comments_", "fallback_comments_"):
        if unique_key.startswith(prefix):
            try:
                return int(unique_key[len(prefix):])
            except ValueError:
                return None

    return None
