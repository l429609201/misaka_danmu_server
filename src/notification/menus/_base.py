"""
通用辅助 Mixin — _submit_auto_import / _submit_edited_import
被 SearchMenuMixin 和 AutoMenuMixin 共用
"""
import hashlib
import logging

from src.notification.base import CommandResult
from src.schemas.import_schemas import EditedImportRequest, ProviderEpisodeInfo
from src.schemas.control import ControlAutoImportRequest, AutoImportSearchType, AutoImportMediaType
from src.tasks import auto_search_and_import_task, edited_import_task

logger = logging.getLogger(__name__)


class ImportBaseMixin:
    """提供自动导入和编辑导入两个公共提交函数"""

    async def _submit_auto_import(self, search_type: str, search_term: str,
                                   media_type: str = None, season: int = None,
                                   episode: str = None,
                                   edit_message_id: int = None) -> CommandResult:
        """构造 ControlAutoImportRequest 并提交到 TaskManager"""
        if not self.task_manager:
            return CommandResult(success=False, text="任务管理器未就绪。")
        try:
            st = AutoImportSearchType(search_type)
            mt = AutoImportMediaType(media_type) if media_type else None
            payload = ControlAutoImportRequest(
                searchType=st, searchTerm=search_term,
                season=season, episode=episode, mediaType=mt,
            )
            title_parts = [f"TG导入: {search_term} ({search_type})"]
            if season is not None:
                title_parts.append(f"S{season:02d}")
            if episode is not None:
                title_parts.append(f"E{episode}")
            task_title = " ".join(title_parts)

            task_coro = lambda session, cb: auto_search_and_import_task(
                payload, cb, session,
                self.config_service, self.scraper_manager,
                self.metadata_manager, self.task_manager,
                ai_service=self.ai_service,
                rate_limiter=self.rate_limiter,
                title_recognition_manager=self.title_recognition_manager,
            )
            task_id, _ = await self.task_manager.submit_task(
                coro_factory=task_coro, title=task_title,
                task_type="auto_import",
                task_parameters=payload.model_dump()
            )
            type_label = {"keyword": "关键词", "tmdb": "TMDB", "tvdb": "TVDB",
                          "douban": "豆瓣", "imdb": "IMDB", "bangumi": "Bangumi"}.get(search_type, search_type)
            mt_label = {"tv_series": "📺 剧集", "movie": "🎬 电影"}.get(media_type or "", "")
            lines = [
                f"✅ 导入任务已提交",
                f"🔍 搜索词：{search_term}（{type_label}）",
            ]
            if mt_label:
                lines.append(f"🗂 类型：{mt_label}")
            if season is not None:
                ep_str = f"  E{episode}" if episode else ""
                lines.append(f"📅 季集：第 {season} 季{ep_str}")
            lines.append(f"🆔 任务ID：`{task_id}`")
            return CommandResult(
                text="\n".join(lines),
                parse_mode="Markdown",
                reply_markup=[[{"text": "📋 查看任务状态", "callback_data": f"task_detail:{task_id}"}]],
                edit_message_id=edit_message_id,
                task_id=task_id,
            )
        except Exception as e:
            logger.error(f"提交自动导入失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ 提交导入任务失败: {e}")

    async def _submit_edited_import(self, provider: str, media_id: str,
                                     title: str, media_type: str, season: int,
                                     year=None, image_url=None,
                                     episodes: list = None,
                                     edit_message_id: int = None) -> CommandResult:
        """构造 EditedImportRequest 并提交到 TaskManager"""
        if not self.task_manager:
            return CommandResult(success=False, text="任务管理器未就绪。")
        try:
            ep_models = []
            for ep in (episodes or []):
                ep_models.append(ProviderEpisodeInfo(
                    provider=ep.get("provider", provider),
                    mediaId=media_id,
                    episodeId=ep.get("episodeId", ""),
                    title=ep.get("title", ""),
                    episodeIndex=ep.get("episodeIndex", 0),
                    url=ep.get("url"),
                ))

            request_data = EditedImportRequest(
                provider=provider,
                mediaId=media_id,
                animeTitle=title,
                mediaType=media_type,
                season=season,
                year=year,
                imageUrl=image_url,
                episodes=ep_models,
            )

            task_title = f"TG编辑导入: {title} ({provider})"
            task_coro = lambda session, cb: edited_import_task(
                request_data=request_data,
                progress_callback=cb,
                session=session,
                config_service=self.config_service,
                manager=self.scraper_manager,
                rate_limiter=self.rate_limiter,
                title_recognition_manager=self.title_recognition_manager,
            )

            episode_indices_str = ",".join(sorted([str(ep.episodeIndex) for ep in ep_models]))
            episodes_hash = hashlib.md5(episode_indices_str.encode('utf-8')).hexdigest()[:8]
            unique_key = f"import-{provider}-{media_id}-{episodes_hash}"

            task_id, _ = await self.task_manager.submit_task(
                task_coro, task_title, unique_key=unique_key
            )
            return CommandResult(
                text=f"✅ 编辑导入任务已提交\n标题: {title}\n源: {provider}\n"
                     f"集数: {len(ep_models)}\n任务ID: {task_id}",
                reply_markup=[[{"text": "📋 查看任务状态", "callback_data": f"task_detail:{task_id}"}]],
                edit_message_id=edit_message_id,
            )
        except Exception as e:
            logger.error(f"提交编辑导入失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ 提交编辑导入失败: {e}")

