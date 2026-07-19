"""
导入前存在性检查独立工具

提供两层 API：
1. check_anime_existence  — 条目级别（找 anime + source）
2. check_episode_existence — 分集级别（比较弹幕数量，决定 import/update/skip）
"""
import logging
from typing import Any, Dict, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import orm_models

logger = logging.getLogger(__name__)

Anime = orm_models.Anime
AnimeSource = orm_models.AnimeSource
AnimeMetadata = orm_models.AnimeMetadata
Episode = orm_models.Episode


def _normalize_title_for_compare(s: Optional[str]) -> str:
    """标题归一化用于一致性校验：去首尾空格、去内部所有空白、统一冒号、转小写。"""
    if not s:
        return ""
    text = str(s).strip().lower().replace("：", ":")
    # 去掉所有空白字符（空格/制表符等），避免"四月是你的谎言"与"四月 是你的谎言"判为不同
    return "".join(text.split())


def _titles_consistent(import_title: Optional[str], existing_title: Optional[str]) -> bool:
    """判断导入标题与库内已有条目标题是否一致（防止 provider+mediaId / 元数据ID 误命中导致串台）。

    why：某些 scraper 可能对不同作品返回相同/占位 mediaId，Stage1 provider+mediaId 命中后
    若不校验标题，会把 B 剧的弹幕/海报/类型串到 A 剧。这里做保守判定：
    - 任一标题为空 → 无法校验，返回 True（不阻断，保持原有行为，避免误伤正常场景）；
    - 归一化后一方包含另一方（含相等）→ 视为一致（兼容"标题"与"标题 第二季"等季度后缀差异）。
    """
    a = _normalize_title_for_compare(import_title)
    b = _normalize_title_for_compare(existing_title)
    if not a or not b:
        return True  # 缺标题时不阻断，保持兼容
    return a == b or a in b or b in a


# ──────────────────────────────────────────────
# 1) 条目级别：查找已有 anime + source
# ──────────────────────────────────────────────

async def _fill_year_on_strong_match(
    session: AsyncSession, anime_id: int, incoming_year: Optional[int]
) -> None:
    """强标识命中后安全补齐年份；已有确定年份时绝不覆盖。"""
    if incoming_year is None:
        return
    anime = await session.get(Anime, anime_id)
    if anime is None:
        return
    if anime.year is None:
        # why：强标识已确认是同一作品，此时补年份不会误合并。
        anime.year = incoming_year
        await session.flush()
        logger.info(f"强标识命中后补齐作品年份: anime_id={anime_id}, year={incoming_year}")
    elif int(anime.year) != int(incoming_year):
        logger.warning(
            f"强标识命中但年份冲突，保留库内年份: anime_id={anime_id}, "
            f"库内year={anime.year}, 导入year={incoming_year}"
        )


def _apply_exact_year(stmt, year: Optional[int]):
    """弱标题匹配显式区分确定年份与未知年份。"""
    return stmt.where(Anime.year.is_(None) if year is None else Anime.year == year)


async def check_anime_existence(
    session: AsyncSession,
    *,
    provider: str,
    media_id: str,
    title: Optional[str] = None,
    media_type: Optional[str] = None,
    season: Optional[int] = None,
    year: Optional[int] = None,
    tmdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
    title_recognition_manager=None,
) -> Dict[str, Any]:
    """按源、元数据、标题三段式查找已有作品，防止重复创建。"""
    not_found = {
        "found": False, "anime_id": None, "source_id": None,
        "stage": "none", "reason": "未命中任何存在性规则",
    }

    # Stage1：同一 provider+mediaId 是强标识；仍校验标题，防御异常源复用占位 mediaId。
    if media_id and str(media_id).strip():
        stmt = (
            select(AnimeSource.id, AnimeSource.animeId, Anime.title)
            .join(Anime, AnimeSource.animeId == Anime.id)
            .where(AnimeSource.providerName == provider, AnimeSource.mediaId == media_id)
        )
        if season is not None:
            stmt = stmt.where(Anime.season == season)
        row = (await session.execute(stmt.order_by(Anime.id).limit(1))).first()
        if row:
            source_id, anime_id, existing_title = row
            if title and not _titles_consistent(title, existing_title):
                logger.warning(
                    f"Stage1 命中但标题不一致，拒绝复用: provider={provider}, mediaId={media_id}, "
                    f"导入title='{title}', 库内title='{existing_title}'"
                )
            else:
                await _fill_year_on_strong_match(session, int(anime_id), year)
                reason = f"弹幕源精确命中(provider={provider}, mediaId={media_id}, season={season})"
                return {"found": True, "anime_id": int(anime_id), "source_id": int(source_id), "stage": "source", "reason": reason}
    else:
        logger.warning(f"Stage1 跳过：mediaId 为空 (provider={provider})")

    # Stage2：元数据 ID 是跨源强标识，命中后允许补齐未知年份。
    for key, value in (("tmdbId", tmdb_id), ("tvdbId", tvdb_id), ("imdbId", imdb_id)):
        if not value or not str(value).strip():
            continue
        stmt = (
            select(Anime.id, Anime.title)
            .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
            .where(getattr(AnimeMetadata, key) == str(value))
        )
        if season is not None:
            stmt = stmt.where(Anime.season == season)
        row = (await session.execute(stmt.order_by(Anime.id).limit(1))).first()
        if row is None:
            continue
        anime_id, existing_title = row
        if title and not _titles_consistent(title, existing_title):
            logger.warning(
                f"Stage2 命中但标题不一致，拒绝复用: {key}={value}, "
                f"导入title='{title}', 库内title='{existing_title}'"
            )
            continue
        await _fill_year_on_strong_match(session, int(anime_id), year)
        reason = f"元数据命中({key}={value}, season={season})"
        return {"found": True, "anime_id": int(anime_id), "source_id": None, "stage": "metadata", "reason": reason}

    # Stage3：弱标题匹配必须同时满足标题、季度、类型和明确的年份状态。
    # why：year=None 只复用未知年份条目，不再猜测并入任一确定年份作品。
    if title and season is not None:
        stmt = select(Anime.id).where(Anime.title == title, Anime.season == season)
        if media_type:
            stmt = stmt.where(Anime.type == media_type)
        stmt = _apply_exact_year(stmt, year)
        anime_ids = (await session.execute(stmt)).scalars().all()
        if anime_ids:
            anime_id = int(min(anime_ids))
            if len(anime_ids) > 1:
                logger.warning(f"Stage3a 发现重复候选 {anime_ids}，暂复用最小ID={anime_id}")
            reason = f"标题精确命中(title={title}, season={season}, type={media_type}, year={year})"
            return {"found": True, "anime_id": anime_id, "source_id": None, "stage": "title", "reason": reason}

        if title_recognition_manager:
            converted_title, converted_season, was_converted, _, _ = (
                await title_recognition_manager.apply_storage_postprocessing(title, season, None)
            )
            if was_converted:
                stmt = select(Anime.id).where(
                    Anime.title == converted_title, Anime.season == converted_season
                )
                if media_type:
                    stmt = stmt.where(Anime.type == media_type)
                stmt = _apply_exact_year(stmt, year)
                anime_ids = (await session.execute(stmt)).scalars().all()
                if anime_ids:
                    anime_id = int(min(anime_ids))
                    reason = (
                        f"识别词转换命中('{title}' S{season:02d} -> "
                        f"'{converted_title}' S{converted_season:02d}, type={media_type}, year={year})"
                    )
                    return {"found": True, "anime_id": anime_id, "source_id": None, "stage": "title", "reason": reason}

    return not_found
# ──────────────────────────────────────────────
# 2) 分集级别：比较弹幕数量，决定 import/update/skip
# ──────────────────────────────────────────────

async def check_episode_existence(
    session: AsyncSession,
    *,
    source_id: int,
    provider_episode_id: str,
    episode_index: int,
    new_comment_count: int,
) -> Dict[str, Any]:
    """
    分集存在性检查 + 弹幕数量比较（获取弹幕后、写库前调用）。

    用 source_id + provider_episode_id 查找已有分集，
    然后比较 commentCount 决定操作。

    Returns:
        {
            "action": str,         # "import" | "update" | "skip"
            "episode_id": Optional[int],
            "existing_count": int,  # 已有弹幕数（0 表示无记录）
            "reason": str,
        }
    """
    # 优先用 provider_episode_id 精确查找，回退到 episode_index
    ep_stmt = (
        select(Episode.id, Episode.commentCount, Episode.danmakuFilePath)
        .where(Episode.sourceId == source_id, Episode.providerEpisodeId == provider_episode_id)
        .limit(1)
    )
    ep_row = (await session.execute(ep_stmt)).first()

    # 如果 provider_episode_id 没命中，用 episode_index 再试
    if ep_row is None:
        ep_stmt2 = (
            select(Episode.id, Episode.commentCount, Episode.danmakuFilePath)
            .where(Episode.sourceId == source_id, Episode.episodeIndex == episode_index)
            .limit(1)
        )
        ep_row = (await session.execute(ep_stmt2)).first()

    if ep_row is None:
        # 分集不存在 → 正常导入
        return {"action": "import", "episode_id": None, "existing_count": 0, "reason": "分集不存在，正常导入"}

    episode_id, existing_count, danmaku_path = ep_row
    existing_count = existing_count or 0

    # 分集存在但没有弹幕（文件路径为空或数量为0）→ 视为新导入
    if not danmaku_path or existing_count == 0:
        return {"action": "import", "episode_id": int(episode_id), "existing_count": 0, "reason": "分集存在但无弹幕，正常导入"}

    # 比较弹幕数量
    if new_comment_count > existing_count:
        detail = f"新弹幕数({new_comment_count}) > 旧弹幕数({existing_count})，需要更新"
        logger.info(f"分集判重: episode_id={episode_id}, {detail}")
        return {"action": "update", "episode_id": int(episode_id), "existing_count": existing_count, "reason": detail}
    else:
        detail = f"新弹幕数({new_comment_count}) <= 旧弹幕数({existing_count})，跳过"
        logger.info(f"分集判重: episode_id={episode_id}, {detail}")
        return {"action": "skip", "episode_id": int(episode_id), "existing_count": existing_count, "reason": detail}

