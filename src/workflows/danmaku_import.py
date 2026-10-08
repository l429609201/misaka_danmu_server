"""
弹幕导入编排层

将原本在 Repository 层的复杂业务逻辑移到这里。
编排层职责：
- 调用 Repository 进行数据库操作
- 调用 Service 进行文件操作
- 调用 Utils 进行数据处理
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.schemas.import_schemas import DanmakuEpisodeCreate
from src.services.file_storage_service import get_file_storage_service, wait_for_settlement
from src.services.service_container import get_database_service
from src.services.cache_service import get_cache_service
from src.utils.misc.common import handle_danmaku_likes
from src.utils.danmaku.xml_generator import generate_xml_from_comments
from src.utils.storage.path_template import generate_danmaku_path
from src.workflows.danmaku_paths import select_write_path

logger = logging.getLogger(__name__)

async def check_anime_existence(
    *, provider: str, media_id: str, title: Optional[str] = None,
    media_type: Optional[str] = None, season: Optional[int] = None,
    year: Optional[int] = None, tmdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None, imdb_id: Optional[str] = None,
    title_recognition_manager: Any = None,
) -> Dict[str, Any]:
    """在调用方 DatabaseService 事务内执行三段式判重，不自行提交。"""
    db = get_database_service()
    identities = []
    if media_id and str(media_id).strip():
        identities.append(("source", {"provider": provider, "media_id": media_id}))
    else:
        logger.warning("Stage1 跳过：mediaId 为空 (provider=%s)", provider)
    for key, value in (("tmdbId", tmdb_id), ("tvdbId", tvdb_id), ("imdbId", imdb_id)):
        if value and str(value).strip():
            identities.append(("metadata", {"metadata_key": key, "metadata_id": value}))
    for stage, identity in identities:
        candidate = await db.anime.find_import_identity(season=season, **identity)
        if candidate is None:
            continue
        # 防止源站复用占位 ID 时把不同作品合并；空标题保持原有兼容语义。
        incoming = "".join(str(title or "").strip().lower().replace("：", ":").split())
        existing = "".join(str(candidate["title"] or "").strip().lower().replace("：", ":").split())
        if incoming and existing and incoming not in existing and existing not in incoming:
            logger.warning("%s 命中但标题不一致，拒绝复用: 导入=%s, 库内=%s",
                           stage, title, candidate["title"])
            continue
        anime_id = candidate["anime_id"]
        if year is not None:
            if candidate["year"] is None:
                await db.anime.update(anime_id, year=year)
            elif int(candidate["year"]) != int(year):
                logger.warning("强标识命中但年份冲突，保留库内年份: anime_id=%s, 库内=%s, 导入=%s",
                               anime_id, candidate["year"], year)
        return {"found": True, "anime_id": anime_id,
                "source_id": candidate.get("source_id"), "stage": stage,
                "reason": f"强标识命中({identity}, season={season})"}

    if title and season is not None:
        ids = await db.anime.find_import_title_ids(title, season, media_type, year)
        if ids:
            if len(ids) > 1:
                logger.warning("Stage3a 发现重复候选 %s，暂复用最小ID=%s", ids, min(ids))
            return {"found": True, "anime_id": min(ids), "source_id": None,
                    "stage": "title", "reason": f"标题精确命中(title={title}, season={season}, year={year})"}
        if title_recognition_manager:
            converted_title, converted_season, was_converted, _, _ = (
                await title_recognition_manager.apply_storage_postprocessing(title, season, None)
            )
            if was_converted:
                ids = await db.anime.find_import_title_ids(
                    converted_title, converted_season, media_type, year,
                )
                if ids:
                    return {"found": True, "anime_id": min(ids), "source_id": None,
                            "stage": "title", "reason": f"识别词转换命中({converted_title}, season={converted_season})"}
    return {"found": False, "anime_id": None, "source_id": None,
            "stage": "none", "reason": "未命中任何存在性规则"}



async def save_danmaku_for_episode(
    episode_id: Optional[int],
    comments: List[Dict[str, Any]],
    config_service: Any = None,
    fire_threshold: int = 1000,
    chat_server: Optional[str] = None,
    *,
    force: bool = False,
    update_fetch_time_on_skip: bool = False,
    create_episode: Optional[DanmakuEpisodeCreate] = None,
    import_if_increased: bool = False,
) -> int:
    """持锁建集并保存，返回写入总数；建集信息与已有分集 ID 必须二选一。

    建集时传入 episode_id=None，作品与源必须已提交；空弹幕不创建空壳。
    本函数自持事务，调用方不得持有目标记录的未提交写事务。
    import_if_increased 保留下载助手按源站 ID 查重、比较原始数量的契约。
    """
    if (episode_id is None) == (create_episode is None):
        raise ValueError("必须且只能提供分集 ID 或建集信息")
    if import_if_increased and (
        create_episode is None or create_episode.skip_existing or force
    ):
        raise ValueError("数量判重导入必须提供建集信息，且不能同时强制覆盖或跳过已有分集")
    if not comments:
        return 0

    db = get_database_service()
    fs = get_file_storage_service()
    backups: Dict[Path, Optional[str]] = {}
    outcome = db.TransactionOutcome()
    # 补偿属于同一次变更，必须在释放锁之前完成。
    async with fs.danmaku_mutation():
        try:
            async with db.transaction(outcome=outcome):
                if create_episode is not None:
                    # 先校验归属，防止错误坐标生成其他作品的确定性分集 ID。
                    source = await db.source.get_by_id(create_episode.source_id)
                    if source is None or source.animeId != create_episode.anime_id:
                        raise ValueError("建集数据源不存在或不属于指定作品")
                    if import_if_increased:
                        # 查询、比较、建集与写入共用一把锁，不能使用锁外判重快照。
                        existing = await db.episode.get_for_import(
                            create_episode.source_id, create_episode.provider_episode_id,
                            create_episode.episode_index,
                        )
                        if existing is not None:
                            episode_id = existing.id
                            old_count = existing.commentCount or 0
                            if existing.danmakuFilePath and old_count > 0 and len(comments) <= old_count:
                                logger.info("分集 %s 原始弹幕数量未增加（新:%s，旧:%s），跳过导入",
                                            episode_id, len(comments), old_count)
                                if update_fetch_time_on_skip:
                                    await db.episode.update_fetch_time(episode_id)
                                return 0
                    # 批量自定义导入的查重必须在锁内，避免检查后被其他请求抢先创建。
                    if create_episode.skip_existing:
                        existing = await db.episode.get_for_import(
                            create_episode.source_id, create_episode.provider_episode_id,
                            create_episode.episode_index,
                        )
                        if existing is not None:
                            return 0
                    # 源站 ID 已命中时复用该记录，避免查重对象与实际写入对象不一致。
                    if episode_id is None:
                        episode_id = await db.episode.create_episode_if_not_exists(
                            anime_id=create_episode.anime_id,
                            source_id=create_episode.source_id,
                            episode_index=create_episode.episode_index,
                            title=create_episode.title,
                            url=create_episode.url,
                            provider_episode_id=create_episode.provider_episode_id,
                            update_existing_title=create_episode.update_existing_title,
                        )
                episode = await db.danmaku_storage.get_by_id(episode_id)
                if not episode:
                    raise ValueError(f"找不到ID为 {episode_id} 的分集")

                likes_fetch_enabled = True
                if config_service is not None:
                    try:
                        likes_fetch_enabled = (await config_service.get(
                            'danmakuLikesFetchEnabled', 'true'
                        )).lower() == 'true'
                    except Exception:
                        pass
                comments = handle_danmaku_likes(
                    list(comments), fire_threshold, enabled=likes_fetch_enabled
                )
                new_comment_count = len(comments)
                old_comment_count = episode.commentCount or 0
                # 原始数量判重已通过时，不再以点赞处理后的数量重复拒绝写入。
                if not force and not import_if_increased and episode.danmakuFilePath and new_comment_count <= old_comment_count:
                    logger.info(
                        f"分集 {episode_id} 弹幕数量未增加 "
                        f"(新:{new_comment_count} <= 旧:{old_comment_count})，跳过刷新"
                    )
                    # 刷新成功但无新增时也应记录抓取时间，避免自动刷新反复触发。
                    if update_fetch_time_on_skip:
                        await db.episode.update_fetch_time(episode_id)
                    return 0

                provider_name = episode.source.providerName
                source_tag_enabled = False
                source_tag_alias = "0"
                if config_service is not None:
                    try:
                        source_tag_enabled = (await config_service.get(
                            'danmakuSourceTagEnabled', 'false'
                        )).lower() == 'true'
                        source_tag_alias = await config_service.get(
                            'danmakuSourceTagAlias', '0'
                        ) or '0'
                    except Exception:
                        pass
                # 空串仅为缺来源的弹幕补 provider；显式别名才替换已有标签。
                effective_source_tag = source_tag_alias if source_tag_enabled else ""
                effective_chat_server = chat_server or "danmaku.misaka.org"
                xml_content = await asyncio.to_thread(
                    generate_xml_from_comments,
                    comments, episode_id, provider_name,
                    effective_chat_server, effective_source_tag,
                )

                if episode.danmakuFilePath:
                    web_path = episode.danmakuFilePath
                    absolute_path = fs.resolve_fs_path(web_path)
                    if absolute_path is None:
                        web_path, absolute_path = await generate_danmaku_path(
                            episode, config_service
                        )
                    else:
                        logger.info(f"刷新弹幕：使用原有路径 {absolute_path}")
                else:
                    web_path, absolute_path = await generate_danmaku_path(
                        episode, config_service
                    )

                # 关联随实际保存一起提交；跳过保存时不产生额外关联变更。
                if create_episode is not None and create_episode.media_server_episode_id:
                    await db.episode.update_media_server_id(
                        episode_id, create_episode.media_server_episode_id,
                    )
                # 写入前检查其他分集引用，共享文件必须分离，不能强制原地覆盖。
                absolute_path = await select_write_path(absolute_path, episode_id)
                web_path = fs.to_web_path(absolute_path)
                await fs.write_with_backup(absolute_path, xml_content, backups)
                await db.danmaku_storage.update_danmaku_info(
                    episode_id=episode_id, danmaku_path=web_path,
                    comment_count=new_comment_count,
                )
                # 文件元信息与抓取时间必须在同一事务内生效。
                await db.episode.update_fetch_time(episode_id)
        except BaseException:
            # 已提交或提交结果未知时不能恢复旧文件，否则可能破坏已生效的数据。
            if outcome.can_restore_files:
                if not await fs.restore_backups(backups):
                    logger.error("弹幕导入补偿未完成，需人工恢复文件")
            elif outcome.status != "committed":
                logger.error("弹幕导入事务结果未知，保留文件待核查：%s", list(backups))
            raise
        finally:
            # 已提交后的取消也要失效缓存；全程持锁且不进入文件补偿分支。
            if outcome.status == "committed" and backups:
                await wait_for_settlement(_invalidate_import_cache(episode_id))
        logger.info(f"弹幕已成功写入文件: {absolute_path} (共 {new_comment_count} 条)")
    return new_comment_count


async def _invalidate_import_cache(episode_id: int) -> None:
    """提交后尽力失效缓存，缓存失败不撤销已提交的文件和数据库。"""
    try:
        cache = get_cache_service()
        await cache.delete(f"fetch_comments_{episode_id}", region="default")
        keys_to_delete = await cache.keys(f"sampled_{episode_id}_*", region="default")
        for key in keys_to_delete:
            await cache.delete(key, region="default")
    except Exception:
        logger.exception("弹幕导入已提交，缓存失效失败：%s", episode_id)
