"""
重关联业务流程编排层

负责协调 Repository（数据库操作）和 FileStorageService（文件操作）
"""

import logging
from typing import Optional

from src.db.orm_models import Episode
from src.schemas import ReassociationResolveRequest
from src.services.file_storage_service import DANMAKU_BASE_DIR
from src.workflows.danmaku_edit_operations import DanmakuEditWriteContext

logger = logging.getLogger(__name__)


async def relocate_episode_danmaku(
    ctx: DanmakuEditWriteContext, episode: Episode, target_anime_id: int,
) -> None:
    """复制弹幕并登记补偿，提交成功后才清理无引用的原文件。"""
    ctx.affected_episodes.add(episode.id)
    if not episode.danmakuFilePath:
        return
    old_path = ctx.fs.resolve_fs_path(episode.danmakuFilePath)
    if old_path is None or not ctx.fs.exists(old_path):
        raise ValueError(f"分集 {episode.id} 的原弹幕文件不存在或路径无效")
    target_path = DANMAKU_BASE_DIR / str(target_anime_id) / f"{episode.id}.xml"
    if ctx.fs.same_file_path(old_path, target_path):
        return
    content = await ctx.fs.read_text(old_path)
    if not content:
        raise ValueError(f"分集 {episode.id} 的弹幕文件读取失败或为空")
    # 复用共享路径保护与文件备份，不在事务提交前移动或删除原文件。
    await ctx.write(target_path, content, episode.id, episode.commentCount or 0)


async def remove_episode_danmaku(ctx: DanmakuEditWriteContext, episode: Episode) -> None:
    """登记待清理路径；数据库删除由仓储完成，文件清理由提交后引用检查决定。"""
    ctx.affected_episodes.add(episode.id)
    if not episode.danmakuFilePath:
        return
    path = ctx.fs.resolve_fs_path(episode.danmakuFilePath)
    if path is None:
        raise ValueError(f"分集 {episode.id} 的弹幕路径无法解析")
    ctx.obsolete.add(ctx.fs.canonical_path(path))


async def reassociate_anime_sources_flow(source_anime_id: int, target_anime_id: int) -> bool:
    """独立事务合并所有源；重复集数沿用保留目标侧的规则。"""
    return await _reassociate(source_anime_id, target_anime_id)


async def reassociate_episodes_with_resolution_flow(
    source_anime_id: int, request: ReassociationResolveRequest,
) -> bool:
    """按用户选择重关联；缺少冲突决策时回滚，不删除未处理数据。"""
    return await _reassociate(source_anime_id, request.targetAnimeId, request)


async def _reassociate(
    source_anime_id: int, target_anime_id: int,
    request: Optional[ReassociationResolveRequest] = None,
) -> bool:
    if source_anime_id == target_anime_id:
        raise ValueError("源作品和目标作品不能相同")
    ctx = DanmakuEditWriteContext()
    async with ctx.transaction():
        repo = ctx.db.reassociation
        source_anime = await repo.get_by_id(source_anime_id)
        target_anime = await repo.get_by_id(target_anime_id)
        if source_anime is None or target_anime is None:
            return False
        resolutions = {r.providerName: r for r in request.resolutions} if request else {}
        if request and len(resolutions) != len(request.resolutions):
            raise ValueError("同一提供商的解决方案不能重复")
        targets = {s.providerName: s for s in target_anime.sources}
        for source in list(source_anime.sources):
            target = targets.get(source.providerName)
            if target is None:
                for episode in list(source.episodes):
                    await relocate_episode_danmaku(ctx, episode, target_anime_id)
                await repo.move_source(source, target_anime)
                # 同一源作品可以包含多个同提供商源，后续源也必须参与冲突检测。
                targets[source.providerName] = source
                continue
            resolution = resolutions.get(source.providerName)
            offset = resolution.sourceOffset if resolution else 0
            decisions = {r.episodeIndex: r.keepSource for r in resolution.episodeResolutions} if resolution else {}
            if resolution and len(decisions) != len(resolution.episodeResolutions):
                raise ValueError("同一分集的解决方案不能重复")
            target_episodes = {ep.episodeIndex: ep for ep in target.episodes}
            for episode in list(source.episodes):
                old_index = episode.episodeIndex
                new_index = old_index + offset
                if new_index < 1:
                    raise ValueError("偏移后的集数必须大于零")
                occupied = target_episodes.get(new_index)
                if occupied is not None:
                    if request is not None and old_index not in decisions:
                        raise ValueError(f"提供商 {source.providerName} 的第 {old_index} 集缺少冲突解决方案")
                    if not decisions.get(old_index, False):
                        await remove_episode_danmaku(ctx, episode)
                        await repo.remove_episode(episode, source)
                        continue
                    await remove_episode_danmaku(ctx, occupied)
                    # 先释放目标唯一键，再迁入源分集，避免 UPDATE 先于 DELETE。
                    await repo.remove_episode(occupied, target)
                await relocate_episode_danmaku(ctx, episode, target_anime_id)
                await repo.move_episode(episode, target, new_index)
                target_episodes[new_index] = episode
            await repo.remove_empty_source(source, source_anime)
        await repo.finish_reassociation(source_anime, target_anime)
    logger.info("作品重关联已提交：%s → %s", source_anime_id, target_anime_id)
    return True

