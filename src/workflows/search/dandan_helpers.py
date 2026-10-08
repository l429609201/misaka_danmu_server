"""
搜索业务流程辅助函数
"""

from datetime import datetime
from typing import List, Dict, Any

from src.core import get_app_timezone
from src.schemas.dandan import DandanSearchAnimeItem
from src.utils.dandan.constants import DANDAN_TYPE_MAPPING, DANDAN_TYPE_DESC_MAPPING


def format_db_results(db_results: List[Dict[str, Any]]) -> List[DandanSearchAnimeItem]:
    """
    将数据库搜索结果转换为 DandanSearchAnimeItem 列表
    
    Args:
        db_results: 数据库查询结果列表
        
    Returns:
        DandanSearchAnimeItem 对象列表
    """
    animes = []
    for res in db_results:
        dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
        dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")
        year = res.get('year')
        start_date_str = None
        
        if year:
            start_date_str = datetime(year, 1, 1, tzinfo=get_app_timezone()).isoformat()
        elif res.get('startDate'):
            start_date_str = res.get('startDate').isoformat()
            
        animes.append(DandanSearchAnimeItem(
            animeId=res['animeId'],
            bangumiId=res.get('bangumiId') or f"A{res['animeId']}",
            animeTitle=res['animeTitle'],
            type=dandan_type,
            typeDescription=dandan_type_desc,
            imageUrl=res.get('imageUrl'),
            startDate=start_date_str,
            year=year,
            episodeCount=res.get('episodeCount', 0),
            rating=0.0,
            isFavorited=False
        ))
    return animes
