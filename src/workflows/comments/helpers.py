"""
弹幕相关辅助函数

包含：
- 请求合并机制（Request Coalescing）
- 弹幕格式处理
"""

import asyncio
import logging
from typing import List, Dict, Any, Tuple

from src.schemas.comments import Comment
from src.utils.danmaku.p_fields import comment_source, normalize_p_attr

logger = logging.getLogger(__name__)

# ============ 请求合并（Request Coalescing）===========
# 同一个 episodeId 同一时间只允许一个刷新/下载任务；
# 其他并发请求（无论来自哪个 token）都等同一个 Event。
_episode_inflight: dict[int, asyncio.Event] = {}
_episode_inflight_lock = asyncio.Lock()


async def coalesce_or_own(episode_id: int) -> Tuple[bool, asyncio.Event]:
    """
    返回: (am_i_owner, event)
    - 如果当前请求是第一个进来的 -> (True, new_event)
    - 如果当前请求需要等待别人 -> (False, existing_event)
    """
    async with _episode_inflight_lock:
        if episode_id in _episode_inflight:
            return (False, _episode_inflight[episode_id])
        else:
            ev = asyncio.Event()
            _episode_inflight[episode_id] = ev
            return (True, ev)


async def release_coalesce(episode_id: int):
    """
    owner 完成任务后，通知所有等待者并移除记录
    """
    async with _episode_inflight_lock:
        event = _episode_inflight.pop(episode_id, None)
    if event:
        event.set()


def process_comments_for_dandanplay(comments_data: List[Dict[str, Any]]) -> List[Comment]:
    """输出时间、模式、颜色和可选来源，不向 API 泄露九段 XML 元数据。"""
    processed_comments = []
    for i, item in enumerate(comments_data):
        p_attr = normalize_p_attr(item.get("p", ""))
        parts = p_attr.split(',')
        api_parts = [parts[0], parts[1], parts[3]]
        source = comment_source(p_attr)
        if source is not None:
            api_parts.append(f"[{source}]")
        processed_comments.append(Comment(
            cid=i, p=','.join(api_parts), m=item.get("m", ""),
        ))
    return processed_comments
