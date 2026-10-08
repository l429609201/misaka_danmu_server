"""自动导入全网搜索前的别名、中文标题和识别词准备。"""
import logging
from typing import Any, Optional, Set, Tuple

from src.schemas import User
from src.services.service_container import get_database_service
from src.utils.data_processing.name_converter import convert_to_chinese_title

logger = logging.getLogger(__name__)


async def prepare_auto_import_search(
    main_title: str, aliases: Set[str], season: Optional[int],
    episode_text: Optional[str], config_service: Any, metadata_manager: Any,
    ai_service: Any, recognition_manager: Any, oauth_user: Optional[User],
) -> Tuple[str, str, Optional[int]]:
    """扩充别名并准备搜索身份，数据库读取结束后才调用外部元数据源。"""
    if metadata_manager:
        try:
            db = get_database_service()
            async with db.transaction():
                admin = await db.user.get_by_username("admin")
                user = User.model_validate(admin, from_attributes=True) if admin else None
            if user:
                supplemental, _, _, _ = await metadata_manager.search_supplemental_sources(main_title, user)
                aliases.update(supplemental)
            else:
                logger.warning("未找到admin用户，跳过元数据源辅助搜索")
        except Exception as exc:
            logger.warning("元数据源辅助搜索失败: %s", exc)
    converted, applied = await convert_to_chinese_title(
        main_title, config_service, metadata_manager, ai_service,
        oauth_user or User(id=0, username="auto_import"),
    )
    if applied:
        logger.info("全自动导入名称转换: '%s' → '%s'", main_title, converted)
        main_title = converted
    search_title, search_season = main_title, season
    if recognition_manager:
        title, episode, mapped_season, applied = await recognition_manager.apply_search_preprocessing(
            main_title, episode_text, season,
        )
        if applied:
            search_title, search_season = title, mapped_season
            logger.info("应用搜索预处理: '%s' -> '%s'", main_title, search_title)
            if episode != episode_text:
                # 保留原策略：搜索集数变换仅作诊断，下载阶段重新执行识别词。
                logger.info("集数预处理: %s -> %s", episode_text, episode)
    return main_title, search_title, search_season
