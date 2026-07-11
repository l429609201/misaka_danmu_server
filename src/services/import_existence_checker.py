"""
导入前存在性检查独立工具

提供两层 API：
1. check_anime_existence  — 条目级别（找 anime + source）
2. check_episode_existence — 分集级别（比较弹幕数量，决定 import/update/skip）
"""
import logging
from typing import Any, Dict, List, Optional

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

async def check_anime_existence(
    session: AsyncSession,
    *,
    provider: str,
    media_id: str,
    title: Optional[str] = None,
    season: Optional[int] = None,
    year: Optional[int] = None,
    tmdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
    title_recognition_manager=None,
) -> Dict[str, Any]:
    """
    三段式条目存在性检查（导入前调用，防止条目重复创建）：

    ① anime_sources.media_id 精确命中（同 provider + mediaId）
    ② anime_metadata 元数据命中（tmdbId/tvdbId/imdbId + season）
    ③ anime.title + season 标题兜底

    Returns:
        {
            "found": bool,
            "anime_id": Optional[int],
            "source_id": Optional[int],  # 仅 stage=source 时有值
            "stage": str,  # "source" | "metadata" | "title" | "none"
            "reason": str,
        }
    """
    NOT_FOUND = {"found": False, "anime_id": None, "source_id": None, "stage": "none", "reason": "未命中任何存在性规则"}

    # ── Stage 1: anime_sources (provider + media_id，可选 season) ──
    # 防御：mediaId 为空/空白时不按其命中——某些 scraper 可能返回空/占位 mediaId，
    # 若据此命中会把不同作品串到同一 anime（海报/类型/标题整条错）。
    if not media_id or not str(media_id).strip():
        logger.warning(f"存在性检查 Stage1 跳过：mediaId 为空 (provider={provider})，避免空 mediaId 误命中串台")
    else:
        src_stmt = (
            select(AnimeSource.id, AnimeSource.animeId, Anime.title)
            .join(Anime, AnimeSource.animeId == Anime.id)
            .where(AnimeSource.providerName == provider, AnimeSource.mediaId == media_id)
        )
        if season is not None:
            src_stmt = src_stmt.where(Anime.season == season)
        src_stmt = src_stmt.limit(1)
        src_row = (await session.execute(src_stmt)).first()
        if src_row:
            source_id, anime_id, existing_title = src_row
            # 标题一致性校验：防止某源对不同作品返回相同 mediaId 导致串台。
            # 传入 title 时才校验（媒体库/webhook 导入均带 title）；不一致则不复用，继续走后续阶段/创建新条目。
            if title and not _titles_consistent(title, existing_title):
                logger.warning(
                    f"存在性检查 Stage1 命中但标题不一致，拒绝复用防串台: "
                    f"provider={provider}, mediaId={media_id}, 导入title='{title}', 库内title='{existing_title}', anime_id={anime_id}"
                )
            else:
                detail = f"弹幕源精确命中(provider={provider}, mediaId={media_id}, season={season})"
                logger.info(f"存在性检查 Stage1 命中: {detail}, anime_id={anime_id}, source_id={source_id}")
                return {"found": True, "anime_id": int(anime_id), "source_id": int(source_id), "stage": "source", "reason": detail}

    # ── Stage 2: anime_metadata (tmdb/tvdb/imdb + season) ──
    metadata_pairs: List[tuple] = [
        ("tmdbId", tmdb_id),
        ("tvdbId", tvdb_id),
        ("imdbId", imdb_id),
    ]
    for key, value in metadata_pairs:
        if not value or not str(value).strip():
            continue
        col = getattr(AnimeMetadata, key)
        md_stmt = (
            select(Anime.id, Anime.title)
            .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
            .where(col == str(value))
        )
        if season is not None:
            md_stmt = md_stmt.where(Anime.season == season)
        md_stmt = md_stmt.limit(1)
        md_row = (await session.execute(md_stmt)).first()
        if md_row is not None:
            anime_id, existing_title = md_row
            # 标题一致性校验：防止导入携带的元数据ID恰好等于库内另一部作品的元数据ID导致串台。
            # season=None（电影等）时未按季度收窄，尤其需要此校验兜底。
            if title and not _titles_consistent(title, existing_title):
                logger.warning(
                    f"存在性检查 Stage2 命中但标题不一致，拒绝复用防串台: "
                    f"{key}={value}, 导入title='{title}', 库内title='{existing_title}', anime_id={anime_id}"
                )
                continue
            detail = f"元数据命中({key}={value}" + (f", season={season})" if season is not None else ")")
            logger.info(f"存在性检查 Stage2 命中: {detail}, anime_id={anime_id}")
            return {"found": True, "anime_id": int(anime_id), "source_id": None, "stage": "metadata", "reason": detail}

    # ── Stage 3: title + season 兜底 ──
    if title and season is not None:
        # 3a: 严格标题匹配
        title_stmt = (
            select(Anime.id)
            .where(Anime.title == title, Anime.season == season)
            .limit(1)
        )
        anime_id = (await session.execute(title_stmt)).scalar_one_or_none()
        if anime_id is not None:
            detail = f"标题精确命中(title={title}, season={season})"
            logger.info(f"存在性检查 Stage3a 命中: {detail}, anime_id={anime_id}")
            return {"found": True, "anime_id": int(anime_id), "source_id": None, "stage": "title", "reason": detail}

        # 3b: 识别词转换后匹配
        if title_recognition_manager:
            converted_title, converted_season, was_converted, _, _ = (
                await title_recognition_manager.apply_storage_postprocessing(title, season, None)
            )
            if was_converted:
                conv_stmt = (
                    select(Anime.id)
                    .where(Anime.title == converted_title, Anime.season == converted_season)
                    .limit(1)
                )
                anime_id = (await session.execute(conv_stmt)).scalar_one_or_none()
                if anime_id is not None:
                    detail = f"识别词转换命中('{title}' S{season:02d} -> '{converted_title}' S{converted_season:02d})"
                    logger.info(f"存在性检查 Stage3b 命中: {detail}, anime_id={anime_id}")
                    return {"found": True, "anime_id": int(anime_id), "source_id": None, "stage": "title", "reason": detail}

    return NOT_FOUND
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

