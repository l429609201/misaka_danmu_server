import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import httpx

from .. import crud, models
from .base import BaseJob
from ..task_manager import TaskManager, TaskSuccess
from ..scraper_manager import ScraperManager

class TmdbAutoMapJob(BaseJob):
    job_type = "tmdb_auto_map"
    job_name = "TMDB自动映射与更新"

    # Add this __init__ to match the base class
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], task_manager: TaskManager, scraper_manager: ScraperManager):
        super().__init__(session_factory, task_manager, scraper_manager)


    async def _create_tmdb_client(self, session: AsyncSession) -> httpx.AsyncClient:
        """Non-FastAPI dependent version of get_tmdb_client."""
        # 修正：移除硬编码的后备URL，改为在数据库查询时提供默认值。
        # 这使得配置逻辑更统一，所有默认值都由 config 表或 crud 函数管理。
        api_key_task = crud.get_config_value(session, "tmdb_api_key", "")
        domain_task = crud.get_config_value(session, "tmdb_api_base_url", "https://api.themoviedb.org")
        api_key, domain = await asyncio.gather(api_key_task, domain_task)

        if not api_key:
            error_msg = "TMDB自动映射任务失败：未配置TMDB API Key。"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        
        cleaned_domain = domain.rstrip('/')
        base_url = cleaned_domain if cleaned_domain.endswith('/3') else f"{cleaned_domain}/3"

        params = {"api_key": api_key}
        headers = {"User-Agent": "DanmuApiServer/1.0 (Scheduled Task)"}
        return httpx.AsyncClient(base_url=base_url, params=params, headers=headers, timeout=30.0)

    async def _update_tmdb_mappings(self, client: httpx.AsyncClient, session: AsyncSession, tmdb_tv_id: int, group_id: str):
        """Non-FastAPI dependent version of update_tmdb_mappings."""
        response = await client.get(f"/tv/episode_group/{group_id}", params={"language": "zh-CN"})
        response.raise_for_status()
        group_details = models.TMDBEpisodeGroupDetails.model_validate(response.json())
        await crud.save_tmdb_episode_group_mappings(
            session=session,
            tmdb_tv_id=tmdb_tv_id,
            group_id=group_id,
            group_details=group_details
        )

    def _select_best_episode_group(self, groups: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """根据用户逻辑选择最佳剧集组。"""
        if not groups:
            return None

        season_x_pattern = re.compile(r"^Season\s+\d+$", re.IGNORECASE)
        filtered_groups = [g for g in groups if not season_x_pattern.match(g.get("name", ""))]

        for group in filtered_groups:
            if group.get("name", "").lower() == "seasons":
                return group

        for group in filtered_groups:
            if "seasons" in group.get("name", "").lower():
                return group
        
        return filtered_groups[0] if filtered_groups else None

    def _parse_tmdb_details_for_aliases(self, details: Dict[str, Any]) -> Dict[str, Any]:
        """从TMDB详情响应中提取并清理别名。"""
        def _clean_movie_title(title: Optional[str]) -> Optional[str]:
            if not title: return None
            phrases_to_remove = ["劇場版", "the movie"]
            cleaned_title = title
            for phrase in phrases_to_remove:
                cleaned_title = re.sub(r'\s*' + re.escape(phrase) + r'\s*:?', '', cleaned_title, flags=re.IGNORECASE)
            cleaned_title = re.sub(r'\s{2,}', ' ', cleaned_title).strip().strip(':- ')
            return cleaned_title

        name_en, name_jp, name_romaji, aliases_cn = None, None, None, []
        original_title = details.get('original_name') or details.get('original_title')
        original_language = details.get('original_language')
        main_title_cn = details.get('name') or details.get('title')

        if alt_titles := details.get('alternative_titles', {}).get('titles', []):
            found_titles = {}
            for alt_title in alt_titles:
                if alt_title.get('iso_3166_1') in ["CN", "HK", "TW"]:
                    aliases_cn.append(alt_title.get('title'))
                elif alt_title.get('iso_3166_1') == "JP":
                    if alt_title.get('type') == "Romaji":
                        if 'romaji' not in found_titles: found_titles['romaji'] = alt_title.get('title')
                    elif not alt_title.get('type'):
                        if 'jp' not in found_titles: found_titles['jp'] = alt_title.get('title')
                elif alt_title.get('iso_3166_1') == "US":
                    if 'en' not in found_titles: found_titles['en'] = alt_title.get('title')
                elif alt_title.get('iso_3166_1') == "GB" and 'en' not in found_titles:
                    found_titles['en'] = alt_title.get('title')
            name_en, name_jp, name_romaji = found_titles.get('en'), found_titles.get('jp'), found_titles.get('romaji')

        if not name_en and original_language == 'en': name_en = original_title
        if not name_jp and original_language == 'ja': name_jp = original_title
        if main_title_cn: aliases_cn.append(main_title_cn)
        
        return {
            "name_en": _clean_movie_title(name_en),
            "name_jp": _clean_movie_title(name_jp),
            "name_romaji": _clean_movie_title(name_romaji),
            "aliases_cn": list(dict.fromkeys([_clean_movie_title(a) for a in aliases_cn if a]))
        }

    async def run(self, session: AsyncSession, progress_callback: Callable):
        """定时任务的核心逻辑。"""
        self.logger.info(f"开始执行 [{self.job_name}] 定时任务...")
        progress_callback(0, "正在初始化...")
        try:
            client = await self._create_tmdb_client(session)
        except ValueError as e:
            self.logger.error(f"无法创建TMDB客户端，任务中止: {e}")
            # 修正：直接抛出异常，让 TaskManager 捕获并标记任务为失败
            raise

        async with client:
            shows_to_update = await crud.get_animes_with_tmdb_id(session)
            total_shows = len(shows_to_update)
            self.logger.info(f"找到 {total_shows} 个带TMDB ID的电视节目需要处理。")
            progress_callback(5, f"找到 {total_shows} 个节目待处理")

            for i, show in enumerate(shows_to_update):
                current_progress = 5 + int((i / total_shows) * 95) if total_shows > 0 else 95
                progress_callback(current_progress, f"正在处理: {show['title']} ({i+1}/{total_shows})")

                anime_id, tmdb_id, title = show['anime_id'], show['tmdb_id'], show['title']
                self.logger.info(f"正在处理: '{title}' (Anime ID: {anime_id}, TMDB ID: {tmdb_id})")
                try:
                    if not show.get('tmdb_episode_group_id'):
                        groups_res = await client.get(f"/tv/{tmdb_id}/episode_groups")
                        if groups_res.status_code == 200:
                            groups = groups_res.json().get("results", [])
                            best_group = self._select_best_episode_group(groups)
                            if best_group:
                                group_id = best_group['id']
                                self.logger.info(f"为 '{title}' 选择了剧集组: '{best_group['name']}' ({group_id})")
                                await crud.update_anime_tmdb_group_id(session, anime_id, group_id)
                                await self._update_tmdb_mappings(client, session, int(tmdb_id), group_id)
                    
                    details_res = await client.get(f"/tv/{tmdb_id}", params={"append_to_response": "alternative_titles"})
                    if details_res.status_code == 200:
                        aliases_to_update = self._parse_tmdb_details_for_aliases(details_res.json())
                        await crud.update_anime_aliases_if_empty(session, anime_id, aliases_to_update)

                    await asyncio.sleep(1)
                except Exception as e:
                    self.logger.error(f"处理 '{title}' (TMDB ID: {tmdb_id}) 时发生错误: {e}", exc_info=True)
        
        self.logger.info(f"定时任务 [{self.job_name}] 执行完毕。")
        # 修正：抛出 TaskSuccess 异常，以便 TaskManager 可以用一个有意义的消息来结束任务
        raise TaskSuccess(f"任务执行完毕，共处理 {total_shows} 个节目。")