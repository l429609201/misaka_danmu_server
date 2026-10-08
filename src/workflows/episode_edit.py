"""分集信息编辑编排，统一协调字段更新、文件补偿和共享路径保护。"""

from src.schemas.anime import EpisodeInfoUpdate
from src.services.config_service import get_config_service
from src.utils.storage.path_template import generate_danmaku_path
from src.workflows.danmaku_edit_operations import DanmakuEditWriteContext


async def update_episode_info(episode_id: int, payload: EpisodeInfoUpdate) -> bool:
    """更新分集信息；改集数时复制到新路径，提交后才清理无引用旧文件。"""
    ctx = DanmakuEditWriteContext()
    async with ctx.transaction():
        episode = await ctx.db.episode.get_by_id_with_relations(episode_id)
        if episode is None:
            return False
        index_changed = episode.episodeIndex != payload.episodeIndex
        old_path = episode.danmakuFilePath
        if index_changed:
            occupied = await ctx.db.episode.get_by_source_and_index(
                episode.sourceId, payload.episodeIndex,
            )
            if occupied is not None and occupied.id != episode_id:
                raise ValueError(f"目标集数 {payload.episodeIndex} 已存在")
        # 仓储只更新字段；保留稳定分集 ID，避免已有匹配及任务引用失效。
        values = {"title": payload.title, "episodeIndex": payload.episodeIndex,
                  "sourceUrl": payload.sourceUrl}
        # 保持旧行为：改集数时按模板迁移，未改集数时才接受手工路径。
        if not index_changed and payload.danmakuFilePath is not None:
            values["danmakuFilePath"] = payload.danmakuFilePath
        if not await ctx.db.episode.update_episode_info(episode_id, values):
            raise ValueError("目标分集已不存在")
        ctx.affected_episodes.add(episode_id)
        if index_changed and old_path:
            path = ctx.fs.resolve_fs_path(old_path)
            if path is None or not ctx.fs.exists(path):
                raise ValueError("原弹幕文件不存在或路径无法解析，拒绝修改集数")
            _, target = await generate_danmaku_path(episode, get_config_service())
            # 路径未变时无须重写 XML；路径变化则复用已有补偿及共享引用检查。
            if not ctx.fs.same_file_path(path, target):
                content = await ctx.fs.read_text(path)
                if not content:
                    raise ValueError("原弹幕文件读取失败或为空，拒绝修改集数")
                await ctx.write(target, content, episode_id, episode.commentCount)
    return True
