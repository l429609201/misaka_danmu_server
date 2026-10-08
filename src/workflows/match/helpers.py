"""
匹配相关辅助函数

包含：
- _build_match_info_from_row: 从查询结果构建 DandanMatchInfo
- _matched_response_from_row: 构建已匹配的响应
- parse_filename_for_match: 从文件名解析番剧信息
"""

import logging
from typing import Dict, Any, Optional

from src.schemas.dandan import DandanMatchInfo, DandanMatchResponse
from src.utils.dandan.constants import DANDAN_TYPE_MAPPING, DANDAN_TYPE_DESC_MAPPING
from src.utils.parsing.filename_parser import parse_filename

logger = logging.getLogger(__name__)


def _build_match_info_from_row(res: Dict[str, Any]) -> DandanMatchInfo:
    """从库内查询结果行(dict)构建单条 DandanMatchInfo。

    统一收口"库内/TMDB映射"等场景里重复的 DandanMatchInfo 构造逻辑，
    避免多处复制粘贴 DANDAN_TYPE_MAPPING 转换 + 字段赋值。
    """
    return DandanMatchInfo(
        episodeId=res['episodeId'],
        animeId=res['animeId'],
        animeTitle=res['animeTitle'],
        episodeTitle=res['episodeTitle'],
        type=DANDAN_TYPE_MAPPING.get(res.get('type'), "other"),
        typeDescription=DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他"),
        imageUrl=res.get('imageUrl'),
    )


def _matched_response_from_row(res: Dict[str, Any], log_label: str) -> DandanMatchResponse:
    """从库内结果行构建"已匹配(isMatched=True)"的 DandanMatchResponse。"""
    response = DandanMatchResponse(isMatched=True, matches=[_build_match_info_from_row(res)])
    logger.info(f"发送匹配响应 ({log_label}): {response.model_dump_json(indent=2)}")
    return response


def parse_filename_for_match(filename: str) -> Optional[Dict[str, Any]]:
    """
    从文件名中解析出番剧标题和集数。
    委托给统一模块 parse_filename()，并转换为 dict 格式以保持向后兼容。
    """
    result = parse_filename(filename)
    if result is None:
        return None

    info: Dict[str, Any] = {
        "title": result.title,
        "season": result.season,
        "episode": result.episode,
    }
    if result.is_movie:
        info["is_movie"] = True
    if result.year:
        info["year"] = result.year
    return info
