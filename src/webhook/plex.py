import logging
import json
import re
from typing import Any, Dict, List
from fastapi import Request, HTTPException, status

from .base import BaseWebhook

logger = logging.getLogger(__name__)

class PlexWebhook(BaseWebhook):
    async def handle(self, request: Request, webhook_source: str):
        # 记录收到webhook请求
        self.logger.info(f"Plex Webhook: 收到请求")
        self.logger.info(f"Headers: {dict(request.headers)}")

        content_type = request.headers.get("content-type", "")

        # 根据Content-Type判断请求格式
        if "multipart/form-data" in content_type:
            # Plex原生webhook - multipart/form-data格式
            self.logger.info("检测到Plex原生webhook格式 (multipart/form-data)")
            try:
                form_data = await request.form()
                payload = dict(form_data)
                self.logger.info(f"Plex原生Webhook表单数据: {payload}")
                await self._handle_plex_native(payload, webhook_source)
            except Exception as e:
                self.logger.error(f"Plex原生Webhook: 无法解析multipart/form-data: {e}")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法解析multipart/form-data")

        elif "application/json" in content_type:
            # Tautulli webhook - JSON格式
            self.logger.info("检测到Tautulli webhook格式 (application/json)")
            try:
                payload = await request.json()
                self.logger.info(f"Tautulli Webhook JSON数据: {json.dumps(payload, indent=2, ensure_ascii=False)}")

                if self._is_tautulli_webhook(payload):
                    await self._handle_tautulli(payload, webhook_source)
                else:
                    self.logger.warning("JSON格式但不是有效的Tautulli webhook格式")
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的Tautulli webhook格式")
            except json.JSONDecodeError as e:
                self.logger.error(f"Tautulli Webhook: 无法解析JSON: {e}")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法解析JSON")
        else:
            self.logger.warning(f"未知的Content-Type: {content_type}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的Content-Type")

    def _is_tautulli_webhook(self, payload: Dict) -> bool:
        """检测是否为Tautulli webhook格式"""
        # Tautulli webhook特征：包含media_type、season、episode字段，以及title或show_name之一
        required_fields = {"media_type", "season", "episode"}
        title_fields = {"title", "show_name"}
        return required_fields.issubset(payload.keys()) and any(field in payload for field in title_fields)

    async def _handle_plex_native(self, payload: Dict, webhook_source: str):
        """处理Plex原生webhook - multipart/form-data格式"""
        # Plex原生webhook发送multipart/form-data格式
        # payload实际上是表单数据，JSON在payload字段中

        # 从表单数据中提取JSON payload
        json_payload_str = payload.get("payload")
        if not json_payload_str:
            self.logger.warning("Plex原生Webhook: 表单数据中缺少 'payload' 字段")
            return

        try:
            json_payload = json.loads(json_payload_str)
        except json.JSONDecodeError as e:
            self.logger.error(f"Plex原生Webhook: 无法解析payload JSON: {e}")
            return

        # 检查事件类型
        event = json_payload.get("event")
        if event != "library.new":
            self.logger.info(f"Plex原生Webhook: 忽略非 'library.new' 事件 (事件类型: {event})")
            return

        # 获取媒体信息
        metadata = json_payload.get("Metadata", {})
        if not metadata:
            self.logger.warning("Plex原生Webhook: 负载中缺少 'Metadata' 信息")
            return

        # 获取媒体类型
        media_type = metadata.get("type")
        if media_type not in ["episode", "movie"]:
            self.logger.info(f"Plex原生Webhook: 忽略非 'episode' 或 'movie' 的媒体项 (类型: {media_type})")
            return

        # 获取用户信息
        account = json_payload.get("Account", {})
        user_name = account.get("title", "Unknown")

        self.logger.info(f"🎬 Plex原生Webhook处理: 用户={user_name}, 媒体类型={media_type}")

        if media_type == "episode":
            # 处理剧集
            series_title = metadata.get("grandparentTitle", "")
            season_number = metadata.get("parentIndex")  # 季数
            episode_number = metadata.get("index")       # 集数

            if not series_title:
                self.logger.warning("Plex原生Webhook: 剧集缺少系列标题")
                return

            if season_number is None or episode_number is None:
                self.logger.warning(f"Plex原生Webhook: 剧集缺少季数或集数信息 (季数: {season_number}, 集数: {episode_number})")
                return

            self.logger.info(f"Plex原生Webhook: 处理剧集 - {series_title} S{season_number:02d}E{episode_number:02d}")

            # 提取Provider IDs
            guid_list = metadata.get("Guid", [])
            provider_ids = self._extract_provider_ids(guid_list)

            await self.dispatch_task(
                task_title=f"{series_title} S{season_number:02d}E{episode_number:02d}",
                unique_key=f"plex_episode_{series_title}_{season_number}_{episode_number}_{user_name}",
                payload={
                    "animeTitle": series_title,
                    "mediaType": "tv_series",
                    "season": season_number,
                    "currentEpisodeIndex": episode_number,
                    "year": metadata.get("year"),
                    "searchKeyword": f"{series_title} S{season_number:02d}E{episode_number:02d}",
                    "doubanId": provider_ids.get("douban"),
                    "tmdbId": provider_ids.get("tmdb"),
                    "imdbId": provider_ids.get("imdb"),
                    "tvdbId": provider_ids.get("tvdb"),
                    "bangumiId": provider_ids.get("bangumi")
                },
                webhook_source=webhook_source
            )

        elif media_type == "movie":
            # 处理电影
            movie_title = metadata.get("title", "")
            year = metadata.get("year")
            
            if not movie_title:
                self.logger.warning("Plex原生Webhook: 电影缺少标题")
                return

            self.logger.info(f"Plex原生Webhook: 处理电影 - {movie_title} ({year})")

            # 提取Provider IDs
            guid_list = metadata.get("Guid", [])
            provider_ids = self._extract_provider_ids(guid_list)

            await self.dispatch_task(
                task_title=f"{movie_title} ({year})" if year else movie_title,
                unique_key=f"plex_movie_{movie_title}_{year}_{user_name}",
                payload={
                    "animeTitle": movie_title,
                    "mediaType": "movie",
                    "season": 1,
                    "currentEpisodeIndex": 1,
                    "year": year,
                    "searchKeyword": f"{movie_title} ({year})" if year else movie_title,
                    "doubanId": provider_ids.get("douban"),
                    "tmdbId": provider_ids.get("tmdb"),
                    "imdbId": provider_ids.get("imdb"),
                    "tvdbId": provider_ids.get("tvdb"),
                    "bangumiId": provider_ids.get("bangumi")
                },
                webhook_source=webhook_source
            )

    async def _handle_tautulli(self, payload: Dict, webhook_source: str):
        """处理Tautulli webhook"""
        # 检查事件类型（如果提供）
        action = payload.get("action", "").lower()
        if action and action != "created":
            self.logger.info(f"Tautulli Webhook: 忽略非新入库事件 (action: {action})")
            return

        # 获取媒体类型
        media_type = payload.get("media_type", "").lower()
        if media_type not in ["episode", "movie", "season"]:
            self.logger.info(f"Tautulli Webhook: 忽略非 'episode'、'movie' 或 'season' 的媒体项 (类型: {media_type})")
            return

        # 获取基本信息 - 优先使用show_name（剧集），回退到title（电影）
        show_name = payload.get("show_name", "")
        title_field = payload.get("title", "")
        title = show_name or title_field
        user_name = payload.get("user_name", "Unknown")

        # 调试日志：显示字段使用情况
        if show_name and title_field:
            self.logger.debug(f"Tautulli Webhook: 使用 show_name='{show_name}'，忽略 title='{title_field}'")
        elif show_name:
            self.logger.debug(f"Tautulli Webhook: 使用 show_name='{show_name}'")
        elif title_field:
            self.logger.debug(f"Tautulli Webhook: 使用 title='{title_field}'")

        self.logger.info(f"📺 Tautulli Webhook处理: 用户={user_name}, 媒体类型={media_type}, 标题={title}")

        if not title:
            self.logger.warning("Tautulli Webhook: 缺少标题信息（show_name 和 title 字段都为空）")
            return

        if media_type in ["episode", "season"]:
            # 处理剧集（单集或多集）
            season_raw = payload.get("season", 1)
            # 确保 season 是整数
            try:
                season = int(season_raw)
            except (ValueError, TypeError):
                self.logger.error(f"无法解析季数 '{season_raw}'，webhook 数据格式错误")
                return

            episode_raw = payload.get("episode", 1)

            # 检查是否为多集格式（包含逗号或连字符）
            if isinstance(episode_raw, str) and (("," in episode_raw) or ("-" in episode_raw and not episode_raw.isdigit())):
                # 多集格式，解析所有集数
                episodes = self._parse_episode_ranges(episode_raw)
                self.logger.info(f"Tautulli Webhook: 检测到多集格式 - {title} S{season:02d} 包含 {len(episodes)} 集")

                # 为每一集创建单独的任务
                for episode_num in episodes:
                    self.logger.info(f"Tautulli Webhook: 处理剧集 - {title} S{season:02d}E{episode_num:02d}")

                    try:
                        await self.dispatch_task(
                            task_title=f"{title} S{season:02d}E{episode_num:02d}",
                            unique_key=f"tautulli_episode_{title}_{season}_{episode_num}_{user_name}",
                            payload={
                                "animeTitle": title,
                                "mediaType": "tv_series",
                                "season": season,
                                "currentEpisodeIndex": episode_num,
                                "year": None,
                                "searchKeyword": f"{title} S{season:02d}E{episode_num:02d}",
                                "doubanId": None,
                                "tmdbId": None,
                                "imdbId": None,
                                "tvdbId": None,
                                "bangumiId": None
                            },
                            webhook_source=webhook_source
                        )
                        self.logger.info(f"Tautulli Webhook: 成功创建任务 - {title} S{season:02d}E{episode_num:02d}")
                    except Exception as e:
                        self.logger.error(f"Tautulli Webhook: 创建任务失败 - {title} S{season:02d}E{episode_num:02d}: {e}", exc_info=True)
                        raise
            else:
                # 单集格式
                episode = int(episode_raw) if isinstance(episode_raw, str) and episode_raw.isdigit() else episode_raw
                self.logger.info(f"Tautulli Webhook: 处理剧集 - {title} S{season:02d}E{episode:02d}")

                try:
                    await self.dispatch_task(
                        task_title=f"{title} S{season:02d}E{episode:02d}",
                        unique_key=f"tautulli_episode_{title}_{season}_{episode}_{user_name}",
                        payload={
                            "animeTitle": title,
                            "mediaType": "tv_series",
                            "season": season,
                            "currentEpisodeIndex": episode,
                            "year": None,
                            "searchKeyword": f"{title} S{season:02d}E{episode:02d}",
                            "doubanId": None,
                            "tmdbId": None,
                            "imdbId": None,
                            "tvdbId": None,
                            "bangumiId": None
                        },
                        webhook_source=webhook_source
                    )
                    self.logger.info(f"Tautulli Webhook: 成功创建任务 - {title} S{season:02d}E{episode:02d}")
                except Exception as e:
                    self.logger.error(f"Tautulli Webhook: 创建任务失败 - {title} S{season:02d}E{episode:02d}: {e}", exc_info=True)
                    raise

        elif media_type == "movie":
            # 处理电影
            release_date = payload.get("release_date", "")
            year = None
            if release_date:
                try:
                    year = int(release_date.split("-")[0])
                except (ValueError, IndexError):
                    pass
            
            self.logger.info(f"Tautulli Webhook: 处理电影 - {title} ({year})")

            try:
                await self.dispatch_task(
                    task_title=f"{title} ({year})" if year else title,
                    unique_key=f"tautulli_movie_{title}_{year}_{user_name}",
                    payload={
                        "animeTitle": title,
                        "mediaType": "movie",
                        "season": 1,
                        "currentEpisodeIndex": 1,
                        "year": year,
                        "searchKeyword": f"{title} ({year})" if year else title,
                        "doubanId": None,
                        "tmdbId": None,
                        "imdbId": None,
                        "tvdbId": None,
                        "bangumiId": None
                    },
                    webhook_source=webhook_source
                )
                self.logger.info(f"Tautulli Webhook: 成功创建任务 - {title} ({year})")
            except Exception as e:
                self.logger.error(f"Tautulli Webhook: 创建任务失败 - {title} ({year}): {e}", exc_info=True)
                raise

    def _extract_provider_ids(self, guid_list: list) -> Dict[str, str]:
        """从Plex的Guid列表中提取各种provider ID"""
        provider_ids = {}
        
        for guid_item in guid_list:
            guid_id = guid_item.get("id", "")
            
            if guid_id.startswith("imdb://"):
                provider_ids["imdb"] = guid_id.replace("imdb://", "")
            elif guid_id.startswith("tmdb://"):
                provider_ids["tmdb"] = guid_id.replace("tmdb://", "")
            elif guid_id.startswith("tvdb://"):
                provider_ids["tvdb"] = guid_id.replace("tvdb://", "")
            elif guid_id.startswith("douban://"):
                provider_ids["douban"] = guid_id.replace("douban://", "")
            elif guid_id.startswith("bangumi://"):
                provider_ids["bangumi"] = guid_id.replace("bangumi://", "")
        
        return provider_ids

    def _parse_episode_ranges(self, episode_str: str) -> List[int]:
        """解析集数范围字符串 — 委托给统一模块"""
        from src.utils.filename_parser import parse_episode_ranges
        return parse_episode_ranges(episode_str)
