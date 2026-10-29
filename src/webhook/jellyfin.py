import logging
import json
from datetime import datetime
from urllib.parse import parse_qs
from fastapi import Request, HTTPException, status

from .base import BaseWebhook

logger = logging.getLogger(__name__)

class JellyfinWebhook(BaseWebhook):
    async def handle(self, request: Request, webhook_source: str):
        # 处理器现在负责解析请求体。
        # 这段逻辑是从主 webhook_api.py 移过来的，专门处理 Jellyfin 的情况。
        content_type = request.headers.get("content-type", "").lower()
        raw_body = await request.body()
        payload = None

        if not raw_body:
            self.logger.warning("Jellyfin Webhook: 收到了一个空的请求体。")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求体为空。")

        try:
            if "application/x-www-form-urlencoded" in content_type:
                form_data = parse_qs(raw_body.decode())
                if 'payload' in form_data:
                    payload_str = form_data['payload'][0]
                    self.logger.info("Jellyfin Webhook: 检测到表单数据，正在解析 'payload' 字段...")
                    payload = json.loads(payload_str)
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="表单数据中不包含 'payload' 字段。"
                    )
            else: # 默认为 JSON
                if "application/json" not in content_type:
                    self.logger.warning(f"Jellyfin Webhook: 未知的 Content-Type: '{content_type}'，将尝试直接解析为 JSON。")
                payload = json.loads(raw_body)
        except json.JSONDecodeError:
            self.logger.error(f"Jellyfin Webhook: 无法将请求体解析为 JSON。Content-Type: '{content_type}'")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求体不是有效的JSON格式。")
        except Exception as e:
            self.logger.error(f"Jellyfin Webhook: 解析负载时发生未知错误: {e}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"无法解析请求体。错误: {e}")

        if not payload:
            self.logger.error("Jellyfin Webhook: 解析后负载为空。")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="解析后负载为空。")

        # 从这里开始，代码与之前相同，处理已解析的 payload
        event_type = payload.get("NotificationType")

        # 处理删除事件
        if event_type == "ItemRemoved":
            await self._handle_delete(payload, webhook_source)
            return

        if event_type not in ["ItemAdded"]:
            logger.info(f"Webhook: 忽略非 'ItemAdded' 或 'ItemRemoved' 的事件 (类型: {event_type})")
            return

        item_type = payload.get("ItemType")
        if item_type not in ["Episode", "Movie"]:
            logger.info(f"Webhook: 忽略非 'Episode' 或 'Movie' 的媒体项 (类型: {item_type})")
            return

        # 提取通用信息
        tmdb_id = payload.get("Provider_tmdb")
        imdb_id = payload.get("Provider_imdb")
        tvdb_id = payload.get("Provider_tvdb")
        douban_id = payload.get("Provider_doubanid")
        bangumi_id = payload.get("Provider_bangumi")
        year = None
        if premiere_date_str := payload.get("PremiereDate"):
            try:
                # Jellyfin's PremiereDate is a full ISO 8601 string
                year = datetime.fromisoformat(premiere_date_str.replace("Z", "+00:00")).year
            except (ValueError, TypeError):
                logger.warning(f"Webhook: 无法从Jellyfin的PremiereDate '{premiere_date_str}' 解析年份。")

        # 根据媒体类型分别处理
        if item_type == "Episode":
            series_title = payload.get("SeriesName")
            # 修正：使用正确的键名来获取季度和集数
            season_number = payload.get("SeasonNumber")
            episode_number = payload.get("EpisodeNumber")

            if not all([series_title, season_number is not None, episode_number is not None]):
                logger.warning(f"Webhook: 忽略一个剧集，因为缺少系列标题、季度或集数信息。")
                return

            logger.info(f"Jellyfin Webhook: 解析到剧集 - 标题: '{series_title}', 类型: Episode, 季: {season_number}, 集: {episode_number}")
            logger.info(f"Webhook: 收到剧集 '{series_title}' S{season_number:02d}E{episode_number:02d}' 的入库通知。")

            task_title = f"Webhook（jellyfin）搜索: {series_title} - S{season_number:02d}E{episode_number:02d}"
            search_keyword = f"{series_title} S{season_number:02d}E{episode_number:02d}"
            media_type = "tv_series"
            anime_title = series_title

        elif item_type == "Movie":
            movie_title = payload.get("Name")
            if not movie_title:
                logger.warning(f"Webhook: 忽略一个电影，因为缺少标题信息。")
                return

            logger.info(f"Jellyfin Webhook: 解析到电影 - 标题: '{movie_title}', 类型: Movie")
            logger.info(f"Webhook: 收到电影 '{movie_title}' 的入库通知。")

            task_title = f"Webhook（jellyfin）搜索: {movie_title}"
            search_keyword = movie_title
            media_type = "movie"
            season_number = 1
            episode_number = 1 # 电影按单集处理
            anime_title = movie_title

        # 提取媒体服务三级 ID（用于删除联动）
        jf_item_id = payload.get("ItemId")
        jf_series_id = payload.get("SeriesId")
        jf_season_id = payload.get("SeasonId")

        # 新逻辑：总是触发全网搜索任务，并附带元数据ID
        unique_key = f"webhook-search-{anime_title}-S{season_number}-E{episode_number}"
        logger.info(f"Webhook: 准备为 '{anime_title}' 创建全网搜索任务，并附加元数据ID (TMDB: {tmdb_id}, IMDb: {imdb_id}, TVDB: {tvdb_id}, Douban: {douban_id})。")

        # 将所有需要的信息打包成 payload
        task_payload = {
            "animeTitle": anime_title, "mediaType": media_type, "season": season_number,
            "currentEpisodeIndex": episode_number, "year": year, "searchKeyword": search_keyword,
            "doubanId": str(douban_id) if douban_id else None,
            "tmdbId": str(tmdb_id) if tmdb_id else None,
            "imdbId": str(imdb_id) if imdb_id else None,
            "tvdbId": str(tvdb_id) if tvdb_id else None,
            "bangumiId": str(bangumi_id) if bangumi_id else None,
            # 媒体服务三级 ID
            "mediaServerType": "jellyfin",
            # Episode → SeriesId; Movie → ItemId 自身（电影没有 SeriesId）
            "mediaServerSeriesId": str(jf_series_id) if jf_series_id else (str(jf_item_id) if jf_item_id and item_type == "Movie" else None),
            "mediaServerSeasonId": str(jf_season_id) if jf_season_id else None,
            # Episode → ItemId; Movie → ItemId（电影按单集处理）
            "mediaServerEpisodeId": str(jf_item_id) if jf_item_id and item_type in ("Episode", "Movie") else None,
        }

        self.add_import(
            task_title=task_title, unique_key=unique_key,
            payload=task_payload, webhook_source=webhook_source,
        )

    async def _handle_delete(self, payload: dict, _webhook_source: str):
        """处理 Jellyfin ItemRemoved 事件，联动删除弹幕数据。"""
        del _webhook_source
        item_type = payload.get("ItemType")
        if item_type not in ["Episode", "Season", "Series", "Movie"]:
            logger.info(f"Jellyfin Webhook 删除: 忽略非 Episode/Season/Series/Movie 类型 (类型: {item_type})")
            return

        item_id = payload.get("ItemId") or ""
        series_id = payload.get("SeriesId")
        season_id = payload.get("SeasonId")
        season_number = payload.get("SeasonNumber") if item_type == "Season" else None
        title = payload.get("SeriesName") or payload.get("Name") or str(item_id)

        logger.info(f"Jellyfin Webhook 删除: 收到 {item_type} 删除事件 - '{title}' (ItemId={item_id})")
        self.add_delete(
            server_type="jellyfin",
            item_type=item_type,
            item_id=str(item_id),
            series_id=str(series_id) if series_id else None,
            season_id=str(season_id) if season_id else None,
            season_number=season_number,
            title=title,
        )
