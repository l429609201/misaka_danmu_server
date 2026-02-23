"""
别名服务模块 - 统一的别名获取、AI验证、保存逻辑

所有需要获取/验证/保存别名的地方都应该调用此模块，而不是各自实现。
"""
import logging
from typing import Optional, Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _pick_best_match(results, title: str, year: Optional[int]):
    """
    从搜索结果中挑选最佳匹配。
    评分规则：标题相似度（0-100）+ 年份匹配加分（+20）。
    要求最低分 70 才算有效匹配。
    """
    from thefuzz import fuzz

    best_item = None
    best_score = 0

    for item in results:
        title_score = fuzz.token_set_ratio(title, item.title)

        year_bonus = 0
        if year and hasattr(item, 'year') and item.year:
            if item.year == year:
                year_bonus = 20
            elif abs(item.year - year) == 1:
                year_bonus = 5
            elif abs(item.year - year) > 3:
                year_bonus = -20

        total_score = title_score + year_bonus

        if total_score > best_score:
            best_score = total_score
            best_item = item

    if best_score >= 70:
        return best_item

    logger.info(f"未找到足够匹配的结果 (最高分: {best_score}, 标题: '{title}', 年份: {year})")
    return None


def extract_aliases_from_details(details) -> Optional[Dict[str, Any]]:
    """
    从 MetadataDetailsResponse 对象中提取别名字典。

    适用于调用方已经拿到 details 对象的场景（如 tmdb_auto_map），
    避免重复请求 API。

    Returns:
        {"name_en": str, "name_jp": str, "name_romaji": str, "aliases_cn": list} 或 None
    """
    if not details:
        return None

    aliases = {
        "name_en": details.nameEn,
        "name_jp": details.nameJp,
        "name_romaji": details.nameRomaji,
        "aliases_cn": details.aliasesCn or []
    }

    if any(aliases.values()):
        return aliases
    return None


async def fetch_aliases(
    title: str,
    media_type: str,
    metadata_manager,
    tmdb_id: Optional[str] = None,
    year: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    从元数据源获取别名。
    优先使用 TMDB ID 直接获取详情（最准确），否则通过标题搜索找最佳匹配。

    Returns:
        {"name_en": str, "name_jp": str, "name_romaji": str, "aliases_cn": list} 或 None
    """
    from src.db import models as db_models
    user = db_models.User(id=0, username="system")
    aliases = None

    # 策略1: TMDB ID 直接获取详情（最准确）
    if tmdb_id and "tmdb" in metadata_manager.sources:
        try:
            media_type_for_tmdb = "movie" if media_type == "movie" else "tv"
            details = await metadata_manager.get_details("tmdb", tmdb_id, user, mediaType=media_type_for_tmdb)
            if details:
                aliases = {
                    "name_en": details.nameEn,
                    "name_jp": details.nameJp,
                    "name_romaji": details.nameRomaji,
                    "aliases_cn": details.aliasesCn or []
                }
                logger.info(f"通过 TMDB ID {tmdb_id} 获取到别名: en={details.nameEn}, jp={details.nameJp}, cn={details.aliasesCn}")
        except Exception as e:
            logger.warning(f"通过 TMDB ID 获取别名失败: {e}")

    # 策略2: 标题搜索找最佳匹配
    if not aliases or not any(aliases.values()):
        aliases = await _search_best_match_aliases(title, year, media_type, metadata_manager, user)

    return aliases


async def _search_best_match_aliases(
    title: str,
    year: Optional[int],
    media_type: str,
    metadata_manager,
    user,
) -> Optional[Dict[str, Any]]:
    """
    通过标题搜索元数据源，按标题相似度+年份匹配找到最佳结果，
    再调 get_details 获取结构化的别名数据。
    """
    media_type_for_tmdb = "movie" if media_type == "movie" else "tv"

    # 尝试 TMDB 搜索
    if "tmdb" in metadata_manager.sources:
        try:
            tmdb_source = metadata_manager.sources["tmdb"]
            results = await tmdb_source.search(title, user, mediaType=media_type_for_tmdb)
            if results:
                best_match = _pick_best_match(results, title, year)
                if best_match:
                    details = await tmdb_source.get_details(best_match.id, user, mediaType=media_type_for_tmdb)
                    if details:
                        logger.info(f"TMDB 搜索最佳匹配: '{details.title}' (year={details.year}, id={details.id})")
                        return {
                            "name_en": details.nameEn,
                            "name_jp": details.nameJp,
                            "name_romaji": details.nameRomaji,
                            "aliases_cn": details.aliasesCn or []
                        }
        except Exception as e:
            logger.warning(f"TMDB 搜索别名失败: {e}")

    # 尝试 Bangumi 搜索
    if "bangumi" in metadata_manager.sources:
        try:
            bgm_source = metadata_manager.sources["bangumi"]
            results = await bgm_source.search(title, user)
            if results:
                best_match = _pick_best_match(results, title, year)
                if best_match:
                    logger.info(f"Bangumi 搜索最佳匹配: '{best_match.title}' (year={best_match.year}, id={best_match.id})")
                    return {
                        "name_en": best_match.nameEn,
                        "name_jp": best_match.nameJp,
                        "name_romaji": best_match.nameRomaji,
                        "aliases_cn": best_match.aliasesCn or []
                    }
        except Exception as e:
            logger.warning(f"Bangumi 搜索别名失败: {e}")

    return None



async def validate_aliases_with_ai(
    title: str,
    year: Optional[int],
    media_type: str,
    aliases_dict: Dict[str, Any],
    ai_matcher,
    ai_alias_correction_enabled: bool = False,
) -> tuple[Dict[str, Any], bool]:
    """
    使用 AI 验证并分类别名（仅对 TV 系列生效，电影跳过）。

    Args:
        title: 作品标题
        year: 年份
        media_type: 类型 (movie / tv_series / tv 等)
        aliases_dict: fetch_aliases 返回的别名字典
        ai_matcher: AIMatcher 实例
        ai_alias_correction_enabled: 是否启用 AI 别名修正（强制更新）

    Returns:
        (validated_aliases_dict, force_update)
    """
    # 电影类型不使用 AI 验证，因为电影标题通常包含系列名+副标题
    is_tv = media_type not in ("movie",)
    if not is_tv or not ai_matcher:
        return aliases_dict, False

    # 收集所有别名
    all_aliases = []
    if aliases_dict.get("name_en"):
        all_aliases.append(aliases_dict["name_en"])
    if aliases_dict.get("name_jp"):
        all_aliases.append(aliases_dict["name_jp"])
    if aliases_dict.get("name_romaji"):
        all_aliases.append(aliases_dict["name_romaji"])
    if aliases_dict.get("aliases_cn"):
        all_aliases.extend(aliases_dict["aliases_cn"])

    if not all_aliases:
        return aliases_dict, False

    try:
        anime_type = "tv_series" if is_tv else "movie"
        logger.info(f"正在使用 AI 验证 '{title}' 的 {len(all_aliases)} 个别名...")
        validated = ai_matcher.validate_aliases(
            title=title,
            year=year,
            anime_type=anime_type,
            aliases=all_aliases
        )

        if validated:
            result = {
                "name_en": validated.get("nameEn"),
                "name_jp": validated.get("nameJp"),
                "name_romaji": validated.get("nameRomaji"),
                "aliases_cn": validated.get("aliasesCn", [])
            }
            force_update = ai_alias_correction_enabled
            logger.info(f"AI 别名验证成功: '{title}'")
            return result, force_update
        else:
            logger.warning(f"AI 别名验证失败，使用原始别名: '{title}'")
            return aliases_dict, False
    except Exception as e:
        logger.warning(f"AI 别名验证异常，使用原始别名: {e}")
        return aliases_dict, False


async def save_aliases(
    session: AsyncSession,
    anime_id: int,
    aliases: Dict[str, Any],
    force_update: bool = False,
) -> Optional[List[str]]:
    """
    保存别名到数据库。

    Returns:
        更新的字段列表，或 None
    """
    from src.db import crud

    if not aliases or not any(aliases.values()):
        return None

    updated_fields = await crud.update_anime_aliases_if_empty(session, anime_id, aliases, force_update=force_update)
    await session.commit()
    return updated_fields


async def fetch_and_save_aliases(
    session: AsyncSession,
    anime_id: int,
    title: str,
    media_type: str,
    metadata_manager,
    tmdb_id: Optional[str] = None,
    year: Optional[int] = None,
    ai_matcher_manager=None,
):
    """
    一站式别名获取+AI验证+保存。
    失败不影响主流程（内部 try/except）。

    适用于 import_core.py 等只需要一行调用的场景。
    """
    try:
        # 1. 获取原始别名
        aliases = await fetch_aliases(title, media_type, metadata_manager, tmdb_id=tmdb_id, year=year)

        force_update = False

        # 2. AI 验证（如果启用）
        if aliases and ai_matcher_manager:
            try:
                from src.db import crud as _crud
                ai_enabled = await ai_matcher_manager.is_enabled()
                ai_recognition = await _crud.get_config_value(session, "aiRecognitionEnabled", "false") == "true"
                ai_correction = await _crud.get_config_value(session, "aiAliasCorrectionEnabled", "false") == "true"

                if ai_enabled and ai_recognition:
                    matcher = await ai_matcher_manager.get_matcher()
                    if matcher:
                        aliases, force_update = await validate_aliases_with_ai(
                            title, year, media_type, aliases, matcher, ai_correction
                        )
            except Exception as e:
                logger.warning(f"AI 别名验证初始化失败，使用原始别名: {e}")

        # 3. 保存
        if aliases and any(aliases.values()):
            updated_fields = await save_aliases(session, anime_id, aliases, force_update=force_update)
            if updated_fields:
                mode = "(AI修正)" if force_update else ""
                logger.info(f"为作品 '{title}' (ID: {anime_id}) 保存了别名{mode}: {', '.join(updated_fields)}")
            else:
                logger.info(f"作品 '{title}' (ID: {anime_id}) 别名已存在或已锁定，跳过更新")
        else:
            logger.info(f"未能为作品 '{title}' (ID: {anime_id}) 获取到任何别名")
    except Exception as e:
        logger.warning(f"自动获取别名失败 (作品: '{title}', ID: {anime_id}): {e}")

