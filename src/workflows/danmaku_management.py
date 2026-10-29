"""管理接口的弹幕读取与全量覆盖编排，不应用播放器输出限制。"""

import asyncio
from math import isfinite
from typing import Any, Dict, List, Optional

from src.services.config_service import ConfigService
from src.services.database_service import DatabaseService
from src.services.file_storage_service import get_file_storage_service
from src.utils.parsing.danmaku_parser import parse_dandan_xml_to_comments
from src.workflows.danmaku_edit_operations import DanmakuEditWriteContext


async def read_episode_comments(
    db: DatabaseService, episode_id: int,
) -> Optional[List[Dict[str, Any]]]:
    """读取分集的全部弹幕；不存在返回 None，无文件返回空列表。"""
    fs = get_file_storage_service()
    # 与编辑/删除共用锁，避免取得路径后文件被移走；文件读取不占用数据库事务。
    async with fs.danmaku_mutation():
        async with db.transaction():
            episode = await db.episode.get_by_id(episode_id)
            if episode is None:
                return None
            file_path = episode.danmakuFilePath
        if not file_path:
            return []
        path = fs.resolve_fs_path(file_path)
        if path is None:
            return []
        content = await fs.read_text(path)
    if not content:
        return []
    return await asyncio.to_thread(parse_dandan_xml_to_comments, content)


async def overwrite_episode_comments(
    episode_id: int, comments: List[Dict[str, Any]], config: ConfigService,
) -> int:
    """完整替换弹幕，复用编辑事务的共享文件保护、回滚补偿与缓存失效。"""
    # 覆盖前验证时间，避免非法数据落盘后被读取端静默跳过。
    prepared = []
    for comment in comments:
        item = comment.copy()
        try:
            timestamp = float(item['p'].split(',')[0])
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ValueError("弹幕时间格式无效") from exc
        if not isfinite(timestamp):
            raise ValueError("弹幕时间必须为有限数值")
        item['t'] = timestamp
        prepared.append(item)

    ctx = DanmakuEditWriteContext()
    # 不先清空旧文件；独立事务完成替换，失败时由现有编辑上下文恢复。
    async with ctx.transaction():
        episode = await ctx.db.episode.get_by_id_with_relations(episode_id)
        if episode is None:
            raise ValueError("分集不存在")
        # 空列表也必须写入空 XML 并将数量更新为 0，而非被导入逻辑跳过。
        await ctx.save(episode, prepared, config)
    return len(prepared)
