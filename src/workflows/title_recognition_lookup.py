"""标题识别查库编排：原始身份优先，转换命中后才二次查询。"""
from typing import Any, Dict, Optional

from src.services.database_service import DatabaseService
from src.workflows.title_recognition import TitleRecognitionWorkflow


async def find_anime_with_recognition(
    db: DatabaseService, title: str, season: Optional[int],
    year: Optional[int] = None,
    recognition: Optional[TitleRecognitionWorkflow] = None,
    source: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """在调用方事务内查找作品，Repository 只接收纯查询字段。"""
    row = await db.anime.find_by_title_season_year(title, season, year)
    if row is not None or recognition is None:
        return row
    converted_title, converted_season, changed, _, _ = (
        await recognition.apply_storage_postprocessing(title, season, source)
    )
    if not changed:
        return None
    return await db.anime.find_by_title_season_year(converted_title, converted_season, year)
