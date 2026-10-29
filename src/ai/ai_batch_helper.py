"""
AI 批量调用辅助模块

提供批量 AI 调用的辅助函数,用于优化性能
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


async def batch_recognize_titles_for_tmdb(
    ai_matcher,
    shows: List[Dict[str, Any]],
    max_concurrent: int = 5
) -> Dict[int, Optional[Dict[str, Any]]]:
    """
    批量识别标题用于 TMDB 搜索
    
    Args:
        ai_matcher: AI 匹配器实例
        shows: 作品列表,每个包含 animeId, title, year, type
        max_concurrent: 最大并发数
    
    Returns:
        {animeId: recognition_result} 映射
    """
    if not ai_matcher:
        logger.warning("AI匹配器未初始化,跳过批量识别")
        return {}
    
    # 准备批量输入
    items = []
    anime_id_map = {}  # {index: animeId}
    
    for idx, show in enumerate(shows):
        items.append({
            "title": show.get("title", ""),
            "year": show.get("year"),
            "type": show.get("type") or "tv_series"
        })
        anime_id_map[idx] = show.get("animeId")
    
    logger.info(f"开始批量识别 {len(items)} 个标题 (最大并发: {max_concurrent})")
    
    # 批量调用
    results = await ai_matcher.batch_recognize_titles(items, max_concurrent)
    
    # 构建结果映射
    result_map = {}
    for idx, result in enumerate(results):
        anime_id = anime_id_map[idx]
        result_map[anime_id] = result
    
    success_count = sum(1 for r in results if r is not None)
    logger.info(f"批量识别完成: {success_count}/{len(items)} 成功")
    
    return result_map


async def optimize_tmdb_scraping_with_batch(
    ai_matcher,
    shows: List[Dict[str, Any]],
    max_concurrent: int = 5
) -> Dict[int, Dict[str, Any]]:
    """
    优化 TMDB 刮削流程,使用批量 AI 调用
    
    Args:
        ai_matcher: AI 匹配器实例
        shows: 需要处理的作品列表
        max_concurrent: 最大并发数
    
    Returns:
        {animeId: {search_title, search_year, search_type, ...}} 映射
    """
    # 筛选需要 AI 识别的作品(没有 TMDB ID 的)
    shows_need_recognition = [
        show for show in shows
        if not show.get("tmdbId")
    ]
    
    if not shows_need_recognition:
        logger.info("没有需要 AI 识别的作品")
        return {}
    
    logger.info(f"找到 {len(shows_need_recognition)} 个作品需要 AI 识别")
    
    # 批量识别
    recognition_results = await batch_recognize_titles_for_tmdb(
        ai_matcher,
        shows_need_recognition,
        max_concurrent
    )
    
    # 处理识别结果
    processed_results = {}
    for anime_id, recognition in recognition_results.items():
        if recognition:
            processed_results[anime_id] = {
                "search_title": recognition.get("search_title"),
                "search_year": recognition.get("year"),
                "search_type": recognition.get("type") or "tv_series",
                "use_episode_group": recognition.get("use_episode_group", False),
                "recognized_season": recognition.get("season")
            }
        else:
            # AI 识别失败,使用原始信息
            original_show = next(
                (s for s in shows_need_recognition if s.get("animeId") == anime_id),
                None
            )
            if original_show:
                processed_results[anime_id] = {
                    "search_title": original_show.get("title"),
                    "search_year": original_show.get("year"),
                    "search_type": original_show.get("type") or "tv_series",
                    "use_episode_group": False,
                    "recognized_season": None
                }
    
    return processed_results


# 使用示例:
"""
# 在 tmdb_auto_map.py 中:

# 1. 批量识别所有需要处理的作品
from ..ai_batch_helper import optimize_tmdb_scraping_with_batch

recognition_map = await optimize_tmdb_scraping_with_batch(
    ai_matcher,
    shows_to_update,
    max_concurrent=5
)

# 2. 在循环中使用预识别的结果
for show in shows_to_update:
    anime_id = show['animeId']
    
    # 获取预识别的结果
    if anime_id in recognition_map:
        recognized = recognition_map[anime_id]
        search_title = recognized['search_title']
        search_year = recognized['search_year']
        search_type = recognized['search_type']
        use_episode_group = recognized['use_episode_group']
        recognized_season = recognized['recognized_season']
    else:
        # 使用原始信息
        search_title = show['title']
        search_year = show.get('year')
        search_type = show.get('type') or 'tv_series'
        use_episode_group = False
        recognized_season = None
    
    # 继续后续处理...
"""

