"""分集重新编号编排：数据库重编号与文件补偿共享事务边界。"""
from typing import Callable, List, Optional
from uuid import uuid4

from src.workflows.danmaku_edit_operations import DanmakuEditWriteContext


async def renumber_episodes(
    progress_callback: Callable, source_id: Optional[int] = None,
    episode_ids: Optional[List[int]] = None, offset: Optional[int] = None,
) -> str:
    """重整一个源或偏移所选分集，失败恢复文件且不关闭外键约束。"""
    if source_id is None and not episode_ids:
        return "没有选中任何分集。"
    ctx = DanmakuEditWriteContext()
    await progress_callback(0, "正在验证分集编号...")
    async with ctx.transaction():
        if source_id is not None:
            episodes = await ctx.db.episode.get_all(source_id=source_id)
            episodes.sort(key=lambda ep: (ep.episodeIndex, ep.id))
            indices = list(range(1, len(episodes) + 1))
        else:
            episodes = await ctx.db.episode.get_by_ids(list(set(episode_ids or [])))
            if len(episodes) != len(set(episode_ids or [])):
                raise ValueError("部分选中的分集未找到")
            source_id = episodes[0].sourceId
            if any(ep.sourceId != source_id for ep in episodes):
                raise ValueError("选中的分集必须属于同一个数据源")
            if offset is None:
                raise ValueError("缺少分集偏移量")
            indices = [ep.episodeIndex + offset for ep in episodes]
        if not episodes:
            return "没有找到分集，无需重整。"
        source = await ctx.db.source.get_by_id(source_id)
        if source is None or source.sourceOrder is None:
            raise ValueError("数据源不存在或缺少持久化顺序")
        mapping = {}
        files = {}
        for episode, index in zip(episodes, indices):
            new_id = int(f"25{source.animeId:06d}{source.sourceOrder:02d}{index:04d}")
            if episode.id == new_id and episode.episodeIndex == index:
                continue
            mapping[episode.id] = index
            if episode.danmakuFilePath:
                path = ctx.fs.resolve_fs_path(episode.danmakuFilePath)
                if path is None or not ctx.fs.exists(path):
                    raise ValueError(f"分集 {episode.id} 原弹幕文件不存在，拒绝迁移")
                content = await ctx.fs.read_text(path)
                if not content:
                    raise ValueError(f"分集 {episode.id} 原弹幕文件读取失败或为空")
                files[episode.id] = (path, content)
        if not mapping:
            return "所有分集顺序和ID都正确，无需调整。"
        await progress_callback(30, f"准备迁移 {len(mapping)} 个分集...")
        targets = await ctx.db.episode.renumber(source_id, mapping)
        ctx.affected_episodes.update(targets)
        ctx.affected_episodes.update(targets.values())
        for old_id, (old_path, content) in files.items():
            target = old_path.with_name(f"{targets[old_id]}.xml")
            # 旧路径可能属于此次互换或其他共享记录；不覆盖，提交后统一清理旧引用。
            if ctx.fs.path_occupied(target) and not ctx.fs.same_file_path(target, old_path):
                target = target.with_name(f"{targets[old_id]}-{uuid4().hex}.xml")
            episode = await ctx.db.episode.get_by_id(targets[old_id])
            fetched_at = episode.fetchedAt
            await ctx.write(target, content, episode.id, episode.commentCount)
            # 重编号不是重新抓取，保留原来的刷新时间。
            await ctx.db.episode.update(episode.id, fetchedAt=fetched_at)
    await progress_callback(100, "分集编号调整完成")
    return f"调整完成，共迁移了 {len(mapping)} 个分集的记录。"
