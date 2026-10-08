import logging
import re
from typing import Optional
from fastapi import Request, HTTPException, status

from .base import BaseWebhook

logger = logging.getLogger(__name__)

_SERIES_RANGE_RE = re.compile(r'(?<!\w)S(\d{1,3})\s*E(\d{1,4})(?:\s*-\s*E(\d{1,4}))?(?![\w-])', re.IGNORECASE)


def _parse_series_episodes(description: str) -> dict[int, list[int]]:
    """按季度提取聚合通知的集数，重复范围合并且拒绝异常大区间。"""
    seasons: dict[int, set[int]] = {}
    for match in _SERIES_RANGE_RE.finditer(description):
        season, first = int(match.group(1)), int(match.group(2))
        last = int(match.group(3)) if match.group(3) else first
        if last < first or last - first >= 1000:
            logger.warning("Emby Webhook: 忽略无效集数范围 S%02d E%02d-E%02d", season, first, last)
            continue
        seasons.setdefault(season, set()).update(range(first, last + 1))
    return {season: sorted(episodes) for season, episodes in sorted(seasons.items())}


def _format_episode_ranges(episodes: list[int]) -> str:
    """把已排序的集数压缩为稳定的任务范围标签和去重键片段。"""
    labels = []
    first = last = episodes[0]
    for episode in episodes[1:]:
        if episode == last + 1:
            last = episode
            continue
        labels.append(f"E{first:02d}" + (f"-E{last:02d}" if last != first else ""))
        first = last = episode
    labels.append(f"E{first:02d}" + (f"-E{last:02d}" if last != first else ""))
    return ",".join(labels)


class EmbyWebhook(BaseWebhook):
    async def handle(self, request: Request, webhook_source: str) -> None:
        """解析 Emby 事件，并按季度提交新增媒体的搜索任务。"""
        # 处理器现在负责解析请求体。
        # Emby 通常发送 application/json。
        try:
            payload = await request.json()
        except Exception:
            self.logger.error("Emby Webhook: 无法解析请求体为JSON。")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求体不是有效的JSON。")

        event_type = payload.get("Event")

        # 处理删除事件
        if event_type == "library.deleted":
            await self._handle_delete(payload, webhook_source)
            return

        # 我们只关心新媒体入库的事件
        if event_type not in ["library.new"]:
            logger.info(f"Webhook: 忽略非 'library.new' 或 'library.deleted' 的事件 (类型: {event_type})")
            return

        item = payload.get("Item", {})
        if not item:
            logger.warning("Emby Webhook: 负载中缺少 'Item' 信息。")
            return

        item_type = item.get("Type")
        if item_type not in ["Episode", "Movie", "Series"]:
            logger.info(f"Webhook: 忽略非 'Episode'、'Movie' 或 'Series' 的媒体项 (类型: {item_type})")
            return

        # 提取通用信息
        provider_ids = item.get("ProviderIds") or {}
        tmdb_id = provider_ids.get("Tmdb")
        imdb_id = provider_ids.get("Imdb") or provider_ids.get("IMDB")
        tvdb_id = provider_ids.get("Tvdb")
        douban_id = provider_ids.get("Douban") or provider_ids.get("DoubanID")
        bangumi_id = provider_ids.get("Bangumi")
        # 统一年份为整数；优先采用媒体项年份，缺失时参考首播日期或显式年份。
        # 单集、电影和 Series 合集共用该值，并通过 task_payload 传入匹配流程。
        year: Optional[int] = None
        for year_value in (item.get("ProductionYear"), item.get("PremiereDate"), payload.get("year")):
            year_text = str(year_value or "").strip().split("-", 1)[0]
            if len(year_text) == 4 and year_text.isdigit() and int(year_text) > 0:
                year = int(year_text)
                break
        logger.info(f"Emby Webhook: 参考年份={year}")

        # 根据媒体类型分别处理
        if item_type == "Episode":
            series_title = item.get("SeriesName")
            # 修正：使用正确的键名来获取季度和集数
            season_number = item.get("ParentIndexNumber")
            episode_number = item.get("IndexNumber")

            if not all([series_title, season_number is not None, episode_number is not None]):
                logger.warning(f"Webhook: 忽略一个剧集，因为缺少系列标题、季度或集数信息。")
                return

            logger.info(f"Emby Webhook: 解析到剧集 - 标题: '{series_title}', 类型: Episode, 季: {season_number}, 集: {episode_number}")
            logger.info(f"Webhook: 收到剧集 '{series_title}' S{season_number:02d}E{episode_number:02d}' 的入库通知。")

            task_title = f"Webhook（emby）搜索: {series_title} - S{season_number:02d}E{episode_number:02d}"
            search_keyword = f"{series_title} S{season_number:02d}E{episode_number:02d}"
            media_type = "tv_series"
            anime_title = series_title

        elif item_type == "Movie":
            movie_title = item.get("Name")
            if not movie_title:
                logger.warning(f"Webhook: 忽略一个电影，因为缺少标题信息。")
                return

            logger.info(f"Emby Webhook: 解析到电影 - 标题: '{movie_title}', 类型: Movie")
            logger.info(f"Webhook: 收到电影 '{movie_title}' 的入库通知。")

            task_title = f"Webhook（emby）搜索: {movie_title}"
            search_keyword = movie_title
            media_type = "movie"
            season_number = 1
            episode_number = 1 # 电影按单集处理
            anime_title = movie_title

        elif item_type == "Series":
            series_title = item.get("Name")
            if not series_title:
                logger.warning("Emby Webhook: 聚合通知缺少 Series 标题，忽略。")
                return

            # 同一通知可能包含多个季，每季分别提交一个选集任务。
            series_selections = _parse_series_episodes(payload.get("Description") or "")
            if not series_selections:
                logger.warning("Emby Webhook: 聚合通知中没有有效的季集范围，忽略。")
                return

            logger.info("Emby Webhook: 解析到聚合通知 - 标题: '%s', 季集: %s", series_title, series_selections)
            episode_number = None
            media_type = "tv_series"
            anime_title = series_title

        # 提取媒体服务三级 ID（用于删除联动）
        emby_item_id = str(item.get("Id", "")) if item.get("Id") else None
        emby_series_id = str(item.get("SeriesId", "")) if item.get("SeriesId") else None
        emby_season_id = str(item.get("SeasonId", "")) if item.get("SeasonId") else None

        # 后续搜索与导入每个任务只接受一个季度；跨季通知必须逐季分发。
        selections = series_selections.items() if item_type == "Series" else [(season_number, None)]
        for selected_season, selected_episodes in selections:
            if item_type == "Series":
                ep_suffix = _format_episode_ranges(selected_episodes)
                task_title = f"Webhook（emby）聚合搜索: {anime_title} - S{selected_season:02d} {ep_suffix}"
                search_keyword = f"{anime_title} S{selected_season:02d}"
            else:
                ep_suffix = f"E{episode_number}"

            unique_key = f"webhook-search-{anime_title}-S{selected_season}-{ep_suffix}"
            logger.info("Webhook: 准备为 '%s' S%02d %s 创建搜索任务 (TMDB: %s, IMDb: %s)",
                        anime_title, selected_season, ep_suffix, tmdb_id, imdb_id)

            task_payload = {
                "animeTitle": anime_title, "mediaType": media_type, "season": selected_season,
                "currentEpisodeIndex": episode_number, "year": year, "searchKeyword": search_keyword,
                "doubanId": str(douban_id) if douban_id else None,
                "tmdbId": str(tmdb_id) if tmdb_id else None,
                "imdbId": str(imdb_id) if imdb_id else None,
                "tvdbId": str(tvdb_id) if tvdb_id else None,
                "bangumiId": str(bangumi_id) if bangumi_id else None,
                "selectedEpisodes": selected_episodes,
                "mediaServerType": "emby",
                "mediaServerSeriesId": emby_series_id or emby_item_id,
                "mediaServerSeasonId": emby_season_id if item_type != "Series" else None,
                "mediaServerEpisodeId": emby_item_id if item_type in ("Episode", "Movie") else None,
            }

            self.add_import(
                task_title=task_title, unique_key=unique_key,
                payload=task_payload, webhook_source=webhook_source,
            )

    async def _handle_delete(self, payload: dict, webhook_source: str):
        """处理 Emby library.deleted 事件，联动删除弹幕数据。"""
        item = payload.get("Item", {})
        if not item:
            logger.info("Emby Webhook 删除: 负载中缺少 'Item' 信息，忽略。")
            return

        item_type = item.get("Type")
        if item_type not in ["Episode", "Season", "Series", "Movie"]:
            logger.info(f"Emby Webhook 删除: 忽略非 Episode/Season/Series/Movie 类型 (类型: {item_type})")
            return

        item_id = str(item.get("Id", ""))
        series_id = str(item.get("SeriesId", "")) if item.get("SeriesId") else None
        season_id = str(item.get("SeasonId", "")) if item.get("SeasonId") else None
        season_number = item.get("ParentIndexNumber") if item_type == "Season" else None
        title = item.get("SeriesName") or item.get("Name") or item_id

        logger.info(f"Emby Webhook 删除: 收到 {item_type} 删除事件 - '{title}' (ItemId={item_id})")
        self.add_delete(
            server_type="emby",
            item_type=item_type,
            item_id=item_id,
            series_id=series_id,
            season_id=season_id,
            season_number=season_number,
            title=title,
        )
