"""别名补充编排：组合元数据服务与数据库服务，不向工具层泄漏业务依赖。"""
import logging
from typing import Any, Dict, Optional

from src.schemas.auth import User
from src.services.metadata_service import MetadataService
from src.services.service_container import get_database_service
from src.utils.data_processing.alias_utils import extract_aliases_from_details, pick_best_match

logger = logging.getLogger(__name__)


async def fetch_aliases(
    title: str,
    media_type: str,
    metadata_manager: MetadataService,
    year: Optional[int] = None,
    tmdb_id: Optional[str] = None,
    bangumi_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """按 TMDB ID、本地索引、标题搜索的既有优先级获取结构化别名。"""
    user = User(id=0, username="system")
    if tmdb_id:
        try:
            # 明确媒体类型，避免同号电影和剧集的详情串用。
            details = await metadata_manager.get_details(
                "tmdb", tmdb_id, user, mediaType=media_type,
            )
            aliases = extract_aliases_from_details(details)
            if aliases:
                return aliases
        except Exception as exc:
            logger.warning("TMDB 别名获取失败 (id=%s): %s", tmdb_id, exc)

    if bangumi_id or title:
        try:
            aliases = None
            if bangumi_id:
                aliases = await metadata_manager.get_offline_aliases_by_bangumi_id(str(bangumi_id))
            if not aliases and title:
                aliases = await metadata_manager.get_offline_aliases_by_title(title)
            if aliases and any(aliases.values()):
                logger.info("bangumi-data 本地命中别名: title='%s'", title)
                return aliases
        except Exception as exc:
            logger.warning("bangumi-data 本地别名查询失败: %s", exc)

    if title:
        try:
            results = await metadata_manager.search("tmdb", title, user, mediaType=media_type)
            best_match = pick_best_match(results or [], title, year)
            if best_match:
                details = await metadata_manager.get_details(
                    "tmdb", best_match.id, user, mediaType=media_type,
                )
                aliases = extract_aliases_from_details(details)
                if aliases:
                    return aliases
        except Exception as exc:
            logger.warning("搜索匹配别名失败: %s", exc)
    logger.info("未能获取别名: title='%s', tmdbId=%s, bangumiId=%s", title, tmdb_id, bangumi_id)
    return None


async def fetch_and_save_aliases(
    anime_id: int,
    title: str,
    media_type: str,
    metadata_manager: MetadataService,
    *,
    year: Optional[int] = None,
    tmdb_id: Optional[str] = None,
    bangumi_id: Optional[str] = None,
) -> bool:
    """为已提交的作品补充别名；返回是否实际更新字段，不启用占位 AI 验证。"""
    aliases = await fetch_aliases(
        title, media_type, metadata_manager,
        year=year, tmdb_id=tmdb_id, bangumi_id=bangumi_id,
    )
    if not aliases:
        return False
    try:
        db = get_database_service()
        # 网络获取在事务外；写入由服务自持短事务提交，不借用任务会话。
        async with db.transaction():
            if await db.anime.get_by_id(anime_id) is None:
                logger.info("作品已不存在，跳过别名补充: anime_id=%s", anime_id)
                return False
            updated = await db.anime.update_aliases_if_empty(anime_id, aliases)
        if updated:
            logger.info("别名已保存: anime_id=%s", anime_id)
        return bool(updated)
    except Exception as exc:
        logger.error("保存别名失败: %s", exc, exc_info=True)
        return False


async def validate_aliases_with_ai(
    title: str,
    year: Optional[int],
    anime_type: str,
    aliases: Dict[str, Any],
    ai_service: Any,
    correction_enabled: bool = False,
) -> tuple[Dict[str, Any], bool]:
    """编排别名验证与字段转换；无有效结果时保留原别名且禁止强制覆盖。"""
    if not ai_service or not aliases:
        return aliases, False
    try:
        candidates = [aliases.get(key) for key in ("name_en", "name_jp", "name_romaji")]
        chinese_aliases = aliases.get("aliases_cn")
        if isinstance(chinese_aliases, list):
            candidates.extend(chinese_aliases)
        candidates = list(dict.fromkeys(
            value.strip() for value in candidates if isinstance(value, str) and value.strip()
        ))
        if not candidates:
            return aliases, False
        result = await ai_service.validate_aliases(title, year, anime_type, candidates)
        if not isinstance(result, dict):
            return aliases, False

        # 只接纳有效非空字段，避免模型缺失字段或空值擦除已有元数据。
        validated = {}
        for source_key, target_key in (
            ("nameEn", "name_en"), ("nameJp", "name_jp"), ("nameRomaji", "name_romaji"),
        ):
            value = result.get(source_key)
            if isinstance(value, str) and value.strip():
                validated[target_key] = value.strip()
        values = result.get("aliasesCn")
        if isinstance(values, list):
            values = list(dict.fromkeys(
                value.strip() for value in values if isinstance(value, str) and value.strip()
            ))
            if values:
                validated["aliases_cn"] = values
        if not validated:
            return aliases, False
        return {**aliases, **validated}, correction_enabled
    except Exception as exc:
        logger.warning("AI别名验证编排失败，保留原始别名: %s", exc)
        return aliases, False
