"""作品身份编排：在创建锁内完成查重、建库和源关联。"""
import asyncio
import logging
from typing import Any, Optional

from src.services.service_container import get_database_service
from src.workflows.danmaku_import import check_anime_existence

logger = logging.getLogger(__name__)
_ANIME_CREATE_LOCK = asyncio.Lock()


async def get_or_create_anime_with_deduplication(
    provider: str,
    mediaId: str,
    title: str,
    mediaType: str,
    season: int,
    imageUrl: Optional[str],
    local_image_path: Optional[str],
    year: Optional[int],
    title_recognition_manager: Any,
    tmdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
) -> int:
    """自持短事务提交作品身份，避免后续并发导入看不到新建记录。"""
    db = get_database_service()
    async with _ANIME_CREATE_LOCK:
        async with db.transaction():
            result = await check_anime_existence(
                provider=provider, media_id=mediaId, title=title,
                media_type=mediaType, season=season, year=year,
                tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id,
                title_recognition_manager=title_recognition_manager,
            )
            if result["found"]:
                anime_id = result["anime_id"]
                logger.info("三段式检查命中(%s): %s，复用anime_id=%s",
                            result["stage"], result["reason"], anime_id)
            else:
                anime = await db.anime.create(
                    title=title, type=mediaType, season=season,
                    year=year, imageUrl=imageUrl,
                )
                anime_id = anime.id
                if local_image_path is not None:
                    await db.anime.update(anime_id, localImagePath=local_image_path)
                logger.info("创建作品: anime_id=%s, title=%s, season=%s, year=%s",
                            anime_id, title, season, year)
            await db.anime.update_metadata_if_empty(
                anime_id, tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id,
            )
            if mediaId and str(mediaId).strip():
                await db.source.link_source_to_anime(anime_id, provider, mediaId)
        # 先提交再释放锁；网络下载保持在本流程外部。
        return anime_id


async def create_preassigned_anime(
    anime_id: int, title: str, media_type: str, season: int,
    image_url: Optional[str], local_image_path: Optional[str], year: Optional[int],
) -> None:
    """提交预分配作品身份，与普通建库共享同一把创建锁。"""
    db = get_database_service()
    async with _ANIME_CREATE_LOCK:
        async with db.transaction():
            await db.anime.create_preassigned(
                anime_id, title, media_type, season, image_url, local_image_path, year,
            )
