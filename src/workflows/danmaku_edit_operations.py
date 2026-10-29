"""弹幕拆分与合并编排，沿用前端字段与历史时间处理规则。"""

import logging
from contextlib import asynccontextmanager
from math import isfinite
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional
from xml.etree import ElementTree

from src.services.service_container import get_database_service
from src.services.cache_service import get_cache_service
from src.services.file_storage_service import get_file_storage_service, wait_for_settlement
from src.utils.danmaku import generate_xml_from_comments
from src.utils.parsing.danmaku_parser import parse_dandan_xml_to_comments
from src.utils.storage.path_template import generate_danmaku_path
from src.workflows.danmaku_paths import cleanup_unreferenced_paths, select_write_path

logger = logging.getLogger(__name__)


class DanmakuEditWriteContext:
    """编辑流程的单次业务状态；文件能力委托文件服务，事务委托数据库服务。"""

    def __init__(self) -> None:
        self.db = get_database_service()
        self.fs = get_file_storage_service()
        self.backups: Dict[Path, Optional[str]] = {}
        self.obsolete: set[Path] = set()
        self.written: set[Path] = set()
        self.affected_episodes: set[int] = set()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None, None]:
        """声明业务事务与补偿范围，不自行实现数据库提交或回滚。"""
        outcome = self.db.TransactionOutcome()
        async with self.fs.danmaku_mutation():
            try:
                async with self.db.transaction(outcome=outcome):
                    yield
            except BaseException:
                if outcome.can_restore_files:
                    if not await self.fs.restore_backups(self.backups):
                        logger.error("弹幕编辑补偿未完成，需人工恢复文件")
                elif outcome.status != "committed":
                    logger.error("弹幕编辑事务结果未知，保留文件待核查：%s", list(self.backups))
                raise
            finally:
                # 已提交后的取消仍须完成收尾，且不能误入恢复旧文件的分支。
                if outcome.status == "committed":
                    await wait_for_settlement(self._after_commit())

    async def _after_commit(self) -> None:
        """持锁完成提交后清理与缓存失效，单项失败不阻断其余收尾。"""
        # 提交后重新读取引用，不以 obsolete/written 的字符串差集决定删除。
        await cleanup_unreferenced_paths(self.obsolete)
        for episode_id in self.affected_episodes:
            try:
                cache = get_cache_service()
                await cache.delete(f"fetch_comments_{episode_id}", region="default")
                for key in await cache.keys(f"sampled_{episode_id}_*", region="default"):
                    await cache.delete(key, region="default")
            except Exception:
                logger.exception("弹幕编辑已提交，缓存失效失败：%s", episode_id)

    async def read(self, episode: Any) -> List[Dict[str, Any]]:
        """严格读取源文件，避免解析失败被当成空弹幕后删除源数据。"""
        self.affected_episodes.add(episode.id)
        path = self.fs.resolve_fs_path(episode.danmakuFilePath)
        if not path or not self.fs.exists(path):
            raise ValueError(f"分集 {episode.id} 的弹幕文件不存在")
        content = await self.fs.read_text(path)
        if not content:
            raise ValueError(f"分集 {episode.id} 的弹幕文件读取失败或为空")
        root = ElementTree.fromstring(content)
        # 播放允许容错，编辑不能把跳过的坏节点静默覆盖丢失。
        nodes = list(root.iter('d'))
        if root.tag != 'i' or len(nodes) != len(root.findall('d')):
            raise ValueError("弹幕 XML 结构异常，拒绝覆盖源文件")
        if any(not node.get('p') or len(node.get('p', '').split(',')) < 3
               or len(node) for node in nodes):
            raise ValueError("弹幕节点参数缺失或含有不支持的子节点")
        comments = parse_dandan_xml_to_comments(content)
        if len(comments) != len(nodes):
            raise ValueError("弹幕解析不完整，拒绝覆盖源文件")
        if any(not isfinite(c['t']) for c in comments):
            raise ValueError("弹幕时间必须为有限数值")
        return comments

    async def write(
        self, path: Path, content: str, episode_id: int, comment_count: int,
    ) -> None:
        """选择独立写入目标，并在同一事务回写路径与数量。"""
        episode = await self.db.episode.get_by_id_with_relations(episode_id)
        if episode is None:
            raise ValueError("目标分集已不存在")
        old_path = self.fs.resolve_fs_path(episode.danmakuFilePath)
        if episode.danmakuFilePath and old_path is None:
            raise ValueError("原弹幕路径无法解析，拒绝修改")
        old_path = self.fs.canonical_path(old_path) if old_path else None
        path = await select_write_path(path, episode_id)
        # 共享目标已分离；无归属的现有文件仍不能因模板碰撞而被覆盖。
        if self.fs.path_occupied(path) and (
            old_path is None or not self.fs.same_file_path(path, old_path)
        ):
            raise ValueError(f"目标弹幕文件已存在：{path}")
        await self.fs.write_with_backup(path, content, self.backups)
        if not await self.db.episode.update_danmaku_info(
            episode_id, self.fs.to_web_path(path), comment_count,
        ):
            raise ValueError("目标分集已不存在")
        self.written.add(path)
        self.affected_episodes.add(episode_id)
        if old_path:
            # 是否仍被引用交由提交后的重新查询决定，包括本集继续引用的情形。
            self.obsolete.add(old_path)

    async def save(self, episode: Any, comments: List[Dict[str, Any]], config: Any) -> None:
        """生成模板路径后复用共享保护写入，统一更新分集元信息。"""
        episode = await self.db.episode.get_by_id_with_relations(episode.id)
        if episode is None:
            raise ValueError("目标分集已不存在")
        _, path = await generate_danmaku_path(episode, config)
        xml = generate_xml_from_comments(comments, episode.id, episode.source.providerName)
        await self.write(path, xml, episode.id, len(comments))

    async def delete(self, episode: Any) -> None:
        """删除数据库记录并延后清理文件，回滚期间保留原文件。"""
        self.affected_episodes.add(episode.id)
        path = self.fs.resolve_fs_path(episode.danmakuFilePath)
        if path:
            self.obsolete.add(path.absolute())
        if not await self.db.episode.delete(episode.id):
            raise ValueError("源分集已不存在")


def shift_comments(comments: List[Dict[str, Any]], seconds: float, clamp: bool = False) -> List[Dict[str, Any]]:
    """复制后同时调整排序时间和 XML 参数，防止多段拆分互相污染。"""
    if not isfinite(seconds):
        raise ValueError("偏移秒数必须为有限数值")
    result = []
    for comment in comments:
        item = comment.copy()
        time = item['t'] + seconds
        time = max(0, time) if clamp else time
        if not isfinite(time):
            raise ValueError("偏移后的时间超出范围")
        parts = item['p'].split(',')
        parts[0] = f"{time:.3f}"
        item.update(p=','.join(parts), t=time)
        result.append(item)
    return result


async def split_episode_danmaku(
    source_episode_id: int, splits: List[Dict[str, Any]], delete_source: bool,
    reset_time: bool, config_service: Any,
) -> Dict[str, Any]:
    """按左闭右开区间拆分，验证完成后才修改数据库和文件。"""
    ctx = DanmakuEditWriteContext()
    try:
        async with ctx.transaction():
            source = await ctx.db.episode.get_by_id_with_relations(source_episode_id)
            if not source:
                raise ValueError("源分集不存在")
            if not splits:
                raise ValueError("没有指定拆分配置")
            comments = await ctx.read(source)
            indices = [s['episodeIndex'] for s in splits]
            if len(set(indices)) != len(indices) or any(i < 1 for i in indices):
                raise ValueError("拆分集数必须为正整数且不能重复")
            batches = []
            for item in splits:
                start, end = item['startTime'], item['endTime']
                if not isfinite(start) or not isfinite(end) or start < 0 or end <= start:
                    raise ValueError("拆分时间范围无效")
                existing = await ctx.db.episode.get_by_source_and_index(source.sourceId, item['episodeIndex'])
                if existing and not (delete_source and existing.id == source.id):
                    raise ValueError(f"集数 {item['episodeIndex']} 已存在")
                selected = [c for c in comments if start <= c['t'] < end]
                if selected:
                    batches.append((item, shift_comments(selected, -start) if reset_time else selected))
            if not batches:
                raise ValueError("指定范围内没有弹幕，保留源分集")
            # 同集数拆分复用原记录，避免唯一键冲突和删除后 ID 被重新分配。
            reused = False
            result = []
            for item, selected in batches:
                title = item.get('title') or f"第{item['episodeIndex']}集"
                if delete_source and item['episodeIndex'] == source.episodeIndex:
                    target = await ctx.db.episode.update(source.id, title=title)
                    reused = True
                else:
                    target = await ctx.db.episode.create(source.sourceId, item['episodeIndex'], title)
                await ctx.save(target, selected, config_service)
                result.append({'episodeId': target.id, 'episodeIndex': item['episodeIndex'], 'commentCount': len(selected)})
            if delete_source and not reused:
                await ctx.delete(source)
        return {'success': True, 'newEpisodes': result}
    except ValueError as exc:
        return {'success': False, 'error': str(exc)}
    except Exception:
        logger.exception("分集拆分失败，已尝试回滚并恢复文件")
        return {'success': False, 'error': '拆分失败，请查看服务日志'}


async def merge_episodes_danmaku(
    source_episodes: List[Dict[str, Any]], target_episode_index: int,
    target_title: str, delete_sources: bool, deduplicate: bool, config_service: Any,
) -> Dict[str, Any]:
    """按请求顺序收集、偏移并可选去重；任一源读取失败都不删除原数据。"""
    ctx = DanmakuEditWriteContext()
    try:
        async with ctx.transaction():
            ids = [s['episodeId'] for s in source_episodes]
            if not ids or len(ids) != len(set(ids)):
                raise ValueError("源分集不能为空或重复")
            if target_episode_index < 1 or not target_title.strip():
                raise ValueError("目标集数或标题无效")
            episodes = {e.id: e for e in await ctx.db.episode.get_by_ids(ids)}
            if len(episodes) != len(ids):
                raise ValueError("部分源分集不存在")
            source_id = episodes[ids[0]].sourceId
            comments = []
            for item in source_episodes:
                source = episodes[item['episodeId']]
                comments.extend(shift_comments(await ctx.read(source), item.get('offsetSeconds', 0)))
            if not comments:
                raise ValueError("没有收集到任何弹幕")
            if deduplicate:
                seen = set()
                unique = []
                for comment in comments:
                    key = f"{comment['t']:.1f}_{comment['m']}"
                    if key not in seen:
                        seen.add(key)
                        unique.append(comment)
                comments = unique
            comments.sort(key=lambda c: c['t'])
            target = await ctx.db.episode.get_by_source_and_index(source_id, target_episode_index)
            if target and not (delete_sources and target.id in episodes):
                raise ValueError(f"目标集数 {target_episode_index} 已存在")
            if target:
                target = await ctx.db.episode.update(target.id, title=target_title)
            else:
                target = await ctx.db.episode.create(source_id, target_episode_index, target_title)
            await ctx.save(target, comments, config_service)
            result = {'success': True, 'newEpisodeId': target.id, 'commentCount': len(comments)}
            if delete_sources:
                for source in episodes.values():
                    if source.id != target.id:
                        await ctx.delete(source)
        return result
    except ValueError as exc:
        return {'success': False, 'error': str(exc)}
    except Exception:
        logger.exception("分集合并失败，已尝试回滚并恢复文件")
        return {'success': False, 'error': '合并失败，请查看服务日志'}
