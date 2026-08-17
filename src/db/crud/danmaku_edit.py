"""
弹幕编辑相关的CRUD操作
- 弹幕详情查询
- 时间偏移调整
- 分集拆分
- 分集合并
"""

import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.utils.danmaku_parser import parse_dandan_xml_to_comments

from ..orm_models import Episode, AnimeSource
from .danmaku import _get_fs_path_from_web_path, _generate_xml_from_comments, _generate_danmaku_path
from .episode import update_episode_danmaku_info

logger = logging.getLogger(__name__)


async def get_danmaku_detail(session: AsyncSession, episode_id: int) -> Optional[Dict[str, Any]]:
    """
    获取指定分集的弹幕详情，包括统计信息、时间分布和弹幕预览
    """
    # 获取分集信息
    stmt = select(Episode).where(Episode.id == episode_id).options(
        selectinload(Episode.source)
    )
    result = await session.execute(stmt)
    episode = result.scalar_one_or_none()
    
    if not episode or not episode.danmakuFilePath:
        return None
    
    # 读取弹幕文件
    absolute_path = _get_fs_path_from_web_path(episode.danmakuFilePath)
    if not absolute_path or not absolute_path.exists():
        return None
    
    try:
        xml_content = absolute_path.read_text(encoding='utf-8')
        comments = parse_dandan_xml_to_comments(xml_content)
    except Exception as e:
        logger.error(f"读取弹幕文件失败: {e}")
        return None
    
    if not comments:
        return {
            "episodeId": episode_id,
            "totalCount": 0,
            "timeRange": {"start": 0, "end": 0},
            "sources": [],
            "distribution": [],
            "comments": []
        }
    
    # 统计来源分布
    source_counts = defaultdict(int)
    for comment in comments:
        p_attr = comment.get('p', '')
        # 提取来源标签 [xxx]
        if '[' in p_attr and ']' in p_attr:
            source_tag = p_attr[p_attr.rfind('[') + 1:p_attr.rfind(']')]
            source_counts[source_tag] += 1
        else:
            source_counts['unknown'] += 1
    
    sources = [{"name": name, "count": count} for name, count in source_counts.items()]
    
    # 计算时间范围
    times = [comment.get('t', 0) for comment in comments]
    time_start = min(times) if times else 0
    time_end = max(times) if times else 0
    
    # 计算每分钟弹幕分布
    distribution = defaultdict(int)
    for comment in comments:
        minute = int(comment.get('t', 0) // 60)
        distribution[minute] += 1
    
    max_minute = int(time_end // 60) + 1
    distribution_list = [{"minute": m, "count": distribution.get(m, 0)} for m in range(max_minute)]
    
    # 弹幕预览（前100条，按时间排序）
    sorted_comments = sorted(comments, key=lambda x: x.get('t', 0))
    preview_comments = []
    for comment in sorted_comments[:100]:
        p_attr = comment.get('p', '')
        source = 'unknown'
        if '[' in p_attr and ']' in p_attr:
            source = p_attr[p_attr.rfind('[') + 1:p_attr.rfind(']')]
        preview_comments.append({
            "time": comment.get('t', 0),
            "content": comment.get('m', ''),
            "source": source
        })
    
    return {
        "episodeId": episode_id,
        "totalCount": len(comments),
        "timeRange": {"start": time_start, "end": time_end},
        "sources": sources,
        "distribution": distribution_list,
        "comments": preview_comments
    }


async def get_danmaku_comments_page(
    session: AsyncSession, 
    episode_id: int, 
    page: int = 1, 
    page_size: int = 100,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None
) -> Dict[str, Any]:
    """
    分页获取弹幕列表，支持时间范围筛选
    """
    # 获取分集信息
    stmt = select(Episode).where(Episode.id == episode_id)
    result = await session.execute(stmt)
    episode = result.scalar_one_or_none()
    
    if not episode or not episode.danmakuFilePath:
        return {"total": 0, "comments": [], "page": page, "pageSize": page_size}
    
    # 读取弹幕文件
    absolute_path = _get_fs_path_from_web_path(episode.danmakuFilePath)
    if not absolute_path or not absolute_path.exists():
        return {"total": 0, "comments": [], "page": page, "pageSize": page_size}
    
    try:
        xml_content = absolute_path.read_text(encoding='utf-8')
        comments = parse_dandan_xml_to_comments(xml_content)
    except Exception as e:
        logger.error(f"读取弹幕文件失败: {e}")
        return {"total": 0, "comments": [], "page": page, "pageSize": page_size}
    
    # 时间范围筛选
    if start_time is not None or end_time is not None:
        filtered = []
        for c in comments:
            t = c.get('t', 0)
            if start_time is not None and t < start_time:
                continue
            if end_time is not None and t > end_time:
                continue
            filtered.append(c)
        comments = filtered
    
    # 按时间排序
    comments = sorted(comments, key=lambda x: x.get('t', 0))
    total = len(comments)
    
    # 分页
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_comments = comments[start_idx:end_idx]
    
    # 格式化输出
    result_comments = []
    for comment in page_comments:
        p_attr = comment.get('p', '')
        source = 'unknown'
        if '[' in p_attr and ']' in p_attr:
            source = p_attr[p_attr.rfind('[') + 1:p_attr.rfind(']')]
        result_comments.append({
            "time": comment.get('t', 0),
            "content": comment.get('m', ''),
            "source": source
        })
    
    return {"total": total, "comments": result_comments, "page": page, "pageSize": page_size}


async def apply_time_offset(
    session: AsyncSession,
    episode_ids: List[int],
    offset_seconds: float
) -> Dict[str, Any]:
    """
    对指定分集的弹幕应用时间偏移
    """
    modified_count = 0
    total_comments = 0

    for episode_id in episode_ids:
        # 获取分集信息
        stmt = select(Episode).where(Episode.id == episode_id).options(
            selectinload(Episode.source)
        )
        result = await session.execute(stmt)
        episode = result.scalar_one_or_none()

        if not episode or not episode.danmakuFilePath:
            continue

        absolute_path = _get_fs_path_from_web_path(episode.danmakuFilePath)
        if not absolute_path or not absolute_path.exists():
            continue

        try:
            xml_content = absolute_path.read_text(encoding='utf-8')
            comments = parse_dandan_xml_to_comments(xml_content)
        except Exception as e:
            logger.error(f"读取弹幕文件失败 episode_id={episode_id}: {e}")
            continue

        if not comments:
            continue

        # 应用时间偏移
        for comment in comments:
            p_attr = comment.get('p', '')
            p_parts = p_attr.split(',')
            if p_parts:
                try:
                    old_time = float(p_parts[0])
                    new_time = max(0, old_time + offset_seconds)  # 确保时间不为负
                    p_parts[0] = f"{new_time:.3f}"
                    comment['p'] = ','.join(p_parts)
                    comment['t'] = new_time
                except ValueError:
                    pass

        # 生成新的XML并写入
        provider_name = episode.source.providerName if episode.source else "misaka"
        xml_content = _generate_xml_from_comments(comments, episode_id, provider_name)

        try:
            absolute_path.write_text(xml_content, encoding='utf-8')
            modified_count += 1
            total_comments += len(comments)
            logger.info(f"分集 {episode_id} 弹幕时间偏移 {offset_seconds}s 完成，共 {len(comments)} 条")
        except Exception as e:
            logger.error(f"写入弹幕文件失败 episode_id={episode_id}: {e}")

    return {"success": True, "modifiedCount": modified_count, "totalComments": total_comments}


async def split_episode_danmaku(
    session: AsyncSession,
    source_episode_id: int,
    splits: List[Dict[str, Any]],
    delete_source: bool = True,
    reset_time: bool = True,
    config_manager = None
) -> Dict[str, Any]:
    """
    将一个分集的弹幕按时间范围拆分到多个新分集

    splits: [
        {"episodeIndex": 1, "startTime": 0, "endTime": 750, "title": "第一部分"},
        {"episodeIndex": 2, "startTime": 750, "endTime": 1500, "title": "第二部分"}
    ]
    """
    from .danmaku import _generate_danmaku_path

    # 获取源分集信息
    stmt = select(Episode).where(Episode.id == source_episode_id).options(
        selectinload(Episode.source).selectinload(AnimeSource.anime)
    )
    result = await session.execute(stmt)
    source_episode = result.scalar_one_or_none()

    if not source_episode or not source_episode.danmakuFilePath:
        return {"success": False, "error": "源分集不存在或没有弹幕文件"}

    absolute_path = _get_fs_path_from_web_path(source_episode.danmakuFilePath)
    if not absolute_path or not absolute_path.exists():
        return {"success": False, "error": "弹幕文件不存在"}

    try:
        xml_content = absolute_path.read_text(encoding='utf-8')
        comments = parse_dandan_xml_to_comments(xml_content)
    except Exception as e:
        return {"success": False, "error": f"读取弹幕文件失败: {e}"}

    if not comments:
        return {"success": False, "error": "弹幕文件为空"}

    source_id = source_episode.sourceId
    provider_name = source_episode.source.providerName if source_episode.source else "misaka"
    new_episodes = []

    # 预先检查所有目标集数是否已存在
    target_indices = [s["episodeIndex"] for s in splits]
    # 如果删除源分集，排除源分集的集数
    exclude_ids = [source_episode_id] if delete_source else []
    existing_check = await session.execute(
        select(Episode).where(
            Episode.sourceId == source_id,
            Episode.episodeIndex.in_(target_indices),
            Episode.id.notin_(exclude_ids) if exclude_ids else True
        )
    )
    existing_episodes = existing_check.scalars().all()
    if existing_episodes:
        existing_indices = [e.episodeIndex for e in existing_episodes]
        return {"success": False, "error": f"集数 {existing_indices} 已存在，请选择其他集数"}

    for split_config in splits:
        episode_index = split_config["episodeIndex"]
        start_time = split_config["startTime"]
        end_time = split_config["endTime"]
        title = split_config.get("title", f"第{episode_index}集")

        # 筛选时间范围内的弹幕
        split_comments = []
        for c in comments:
            t = c.get('t', 0)
            if start_time <= t < end_time:
                new_comment = c.copy()
                if reset_time:
                    # 重置时间，从0开始
                    new_time = t - start_time
                    p_parts = new_comment.get('p', '').split(',')
                    if p_parts:
                        p_parts[0] = f"{new_time:.3f}"
                        new_comment['p'] = ','.join(p_parts)
                        new_comment['t'] = new_time
                split_comments.append(new_comment)

        if not split_comments:
            continue

        # 创建新分集
        new_episode = Episode(
            sourceId=source_id,
            episodeIndex=episode_index,
            title=title,
            commentCount=len(split_comments)
        )
        session.add(new_episode)
        await session.flush()

        # 生成弹幕文件路径
        web_path, file_path = await _generate_danmaku_path(session, new_episode, config_manager)

        # 生成并写入XML
        xml_content = _generate_xml_from_comments(split_comments, new_episode.id, provider_name)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(xml_content, encoding='utf-8')

        # 更新分集信息
        await update_episode_danmaku_info(session, new_episode.id, web_path, len(split_comments))

        new_episodes.append({
            "episodeId": new_episode.id,
            "episodeIndex": episode_index,
            "commentCount": len(split_comments)
        })

        logger.info(f"拆分创建新分集: episode_id={new_episode.id}, index={episode_index}, comments={len(split_comments)}")

    # 删除源分集
    if delete_source and new_episodes:
        # 删除弹幕文件
        if absolute_path.exists():
            absolute_path.unlink()
        # 删除数据库记录
        await session.delete(source_episode)
        logger.info(f"已删除源分集: episode_id={source_episode_id}")

    await session.commit()

    return {"success": True, "newEpisodes": new_episodes}


async def merge_episodes_danmaku(
    session: AsyncSession,
    source_episodes: List[Dict[str, Any]],
    target_episode_index: int,
    target_title: str,
    delete_sources: bool = True,
    deduplicate: bool = False,
    config_manager = None
) -> Dict[str, Any]:
    """
    将多个分集的弹幕合并到一个新分集

    source_episodes: [
        {"episodeId": 1, "offsetSeconds": 0},
        {"episodeId": 2, "offsetSeconds": 1500},
        {"episodeId": 3, "offsetSeconds": 3000}
    ]
    """
    from .danmaku import _generate_danmaku_path

    if not source_episodes:
        return {"success": False, "error": "没有指定源分集"}

    # 获取第一个源分集的source信息
    first_episode_id = source_episodes[0]["episodeId"]
    stmt = select(Episode).where(Episode.id == first_episode_id).options(
        selectinload(Episode.source).selectinload(AnimeSource.anime)
    )
    result = await session.execute(stmt)
    first_episode = result.scalar_one_or_none()

    if not first_episode:
        return {"success": False, "error": "源分集不存在"}

    source_id = first_episode.sourceId
    provider_name = first_episode.source.providerName if first_episode.source else "misaka"

    # 收集所有弹幕
    all_comments = []
    episodes_to_delete = []

    for source_config in source_episodes:
        episode_id = source_config["episodeId"]
        offset_seconds = source_config.get("offsetSeconds", 0)

        stmt = select(Episode).where(Episode.id == episode_id)
        result = await session.execute(stmt)
        episode = result.scalar_one_or_none()

        if not episode or not episode.danmakuFilePath:
            continue

        absolute_path = _get_fs_path_from_web_path(episode.danmakuFilePath)
        if not absolute_path or not absolute_path.exists():
            continue

        try:
            xml_content = absolute_path.read_text(encoding='utf-8')
            comments = parse_dandan_xml_to_comments(xml_content)
        except Exception as e:
            logger.error(f"读取弹幕文件失败 episode_id={episode_id}: {e}")
            continue

        # 应用时间偏移
        for comment in comments:
            new_comment = comment.copy()
            p_parts = new_comment.get('p', '').split(',')
            if p_parts:
                try:
                    old_time = float(p_parts[0])
                    new_time = old_time + offset_seconds
                    p_parts[0] = f"{new_time:.3f}"
                    new_comment['p'] = ','.join(p_parts)
                    new_comment['t'] = new_time
                except ValueError:
                    pass
            all_comments.append(new_comment)

        episodes_to_delete.append((episode, absolute_path))
        logger.info(f"收集分集 {episode_id} 弹幕 {len(comments)} 条，偏移 {offset_seconds}s")

    if not all_comments:
        return {"success": False, "error": "没有收集到任何弹幕"}

    # 去重
    if deduplicate:
        seen = set()
        unique_comments = []
        for c in all_comments:
            key = f"{c.get('t', 0):.1f}_{c.get('m', '')}"
            if key not in seen:
                seen.add(key)
                unique_comments.append(c)
        all_comments = unique_comments
        logger.info(f"去重后弹幕数: {len(all_comments)}")

    # 按时间排序
    all_comments.sort(key=lambda x: x.get('t', 0))

    # 检查目标集数是否与某个源分集的集数相同（可以复用）
    reuse_episode = None
    if delete_sources:
        for episode, _ in episodes_to_delete:
            if episode.episodeIndex == target_episode_index:
                reuse_episode = episode
                break

    if reuse_episode:
        # 复用已有分集，更新其信息
        new_episode = reuse_episode
        new_episode.title = target_title
        new_episode.commentCount = len(all_comments)

        # 从删除列表中移除（因为要复用）
        episodes_to_delete = [(e, p) for e, p in episodes_to_delete if e.id != reuse_episode.id]

        logger.info(f"复用已有分集: episode_id={new_episode.id}, index={target_episode_index}")
    else:
        # 检查目标集数是否已存在（排除将被删除的源分集）
        source_episode_ids = [e.id for e, _ in episodes_to_delete] if delete_sources else []
        existing_check = await session.execute(
            select(Episode).where(
                Episode.sourceId == source_id,
                Episode.episodeIndex == target_episode_index,
                Episode.id.notin_(source_episode_ids) if source_episode_ids else True
            )
        )
        existing_episode = existing_check.scalar_one_or_none()
        if existing_episode:
            return {"success": False, "error": f"目标集数 {target_episode_index} 已存在，请选择其他集数"}

        # 创建新分集
        new_episode = Episode(
            sourceId=source_id,
            episodeIndex=target_episode_index,
            title=target_title,
            commentCount=len(all_comments)
        )
        session.add(new_episode)
        await session.flush()

        logger.info(f"合并创建新分集: episode_id={new_episode.id}, index={target_episode_index}")

    # 生成弹幕文件路径
    web_path, file_path = await _generate_danmaku_path(session, new_episode, config_manager)

    # 生成并写入XML
    xml_content = _generate_xml_from_comments(all_comments, new_episode.id, provider_name)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(xml_content, encoding='utf-8')

    # 更新分集信息
    await update_episode_danmaku_info(session, new_episode.id, web_path, len(all_comments))

    # 删除源分集
    if delete_sources:
        for episode, file_path in episodes_to_delete:
            if file_path.exists():
                file_path.unlink()
            await session.delete(episode)
            logger.info(f"已删除源分集: episode_id={episode.id}")

    await session.commit()

    return {
        "success": True,
        "newEpisodeId": new_episode.id,
        "commentCount": len(all_comments)
    }