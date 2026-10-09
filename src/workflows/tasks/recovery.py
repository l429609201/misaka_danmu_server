"""具体任务持久化参数的业务恢复组装。"""

import logging
from typing import Any, Callable, Dict, Optional

from src.schemas.control import ControlAutoImportRequest
from src.schemas.import_schemas import EditedImportRequest


class TaskRecoveryResolver:
    """把具体任务参数恢复为已注册处理器的协程工厂。"""

    def __init__(self, task_manager, dependencies: Dict[str, Any]) -> None:
        self.task_manager = task_manager
        self.dependencies = dependencies
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def resolve_queue(task_type: str, queue_type: str) -> str:
        """兼容旧自动导入父任务的下载队列记录。"""
        return "search" if task_type == "auto_import" and queue_type == "download" else queue_type

    async def rebuild(self, task_type: str, task_parameters: Dict) -> Optional[Callable]:
        """根据任务类型和参数重建协程工厂

        Args:
            task_type: 任务类型
            task_parameters: 任务参数

        Returns:
            协程工厂函数，如果无法重建则返回None
        """
        if not self.dependencies:
            return None

        deps = self.dependencies
        scraper_manager = deps.get("scraper_manager")
        rate_limiter = deps.get("rate_limiter")
        metadata_manager = deps.get("metadata_manager")
        # 恢复任务沿用启动时注册的共享 AI 服务。
        ai_service = deps.get("ai_service")
        title_recognition_manager = deps.get("title_recognition_manager")

        try:
            if task_type == "local_danmaku_import":
                # 本地导入只恢复持久化参数，无需抓取器或外部会话快照。
                return self.task_manager.build_task_coro_factory(
                    "local_danmaku_import",
                    item_ids=task_parameters["item_ids"],
                    import_options=task_parameters.get("import_options", {}),
                )
            if task_type == "generic_import":
                # 恢复与首次派发共用注册处理器，服务层不反向导入任务包。
                return self.task_manager.build_task_coro_factory(
                    "generic_import",
                    provider=task_parameters.get("provider"),
                    mediaId=task_parameters.get("mediaId"),
                    animeTitle=task_parameters.get("animeTitle"),
                    mediaType=task_parameters.get("mediaType"),
                    season=task_parameters.get("season"),
                    year=task_parameters.get("year"),
                    currentEpisodeIndex=task_parameters.get("currentEpisodeIndex"),
                    imageUrl=task_parameters.get("imageUrl"),
                    config_service=self.task_manager.config_service,
                    metadata_manager=metadata_manager,
                    manager=scraper_manager,
                    task_manager=self.task_manager,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                    doubanId=task_parameters.get("doubanId"),
                    tmdbId=task_parameters.get("tmdbId"),
                    imdbId=task_parameters.get("imdbId"),
                    tvdbId=task_parameters.get("tvdbId"),
                    bangumiId=task_parameters.get("bangumiId"),
                    # 恢复时保留多集选择，不能降级为全季导入。
                    selectedEpisodes=task_parameters.get("selectedEpisodes"),
                    mediaServerType=task_parameters.get("mediaServerType"),
                    mediaServerSeriesId=task_parameters.get("mediaServerSeriesId"),
                    mediaServerSeasonId=task_parameters.get("mediaServerSeasonId"),
                    mediaServerEpisodeId=task_parameters.get("mediaServerEpisodeId"),
                    fallbackCandidates=task_parameters.get("fallbackCandidates"),
                    enable_incremental_refresh=bool(task_parameters.get("enableIncrementalRefresh", False)),
                )

            elif task_type == "webhook_search":
                return self.task_manager.build_task_coro_factory(
                    "webhook_search",
                    animeTitle=task_parameters.get("animeTitle"),
                    mediaType=task_parameters.get("mediaType"),
                    season=task_parameters.get("season"),
                    currentEpisodeIndex=task_parameters.get("currentEpisodeIndex"),
                    searchKeyword=task_parameters.get("searchKeyword"),
                    doubanId=task_parameters.get("doubanId"),
                    tmdbId=task_parameters.get("tmdbId"),
                    imdbId=task_parameters.get("imdbId"),
                    tvdbId=task_parameters.get("tvdbId"),
                    bangumiId=task_parameters.get("bangumiId"),
                    webhookSource=task_parameters.get("webhookSource"),
                    imageUrl=task_parameters.get("imageUrl"),
                    year=task_parameters.get("year"),
                    manager=scraper_manager,
                    task_manager=self.task_manager,
                    metadata_manager=metadata_manager,
                    config_service=self.task_manager.config_service,
                    ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                    selectedEpisodes=task_parameters.get("selectedEpisodes"),
                    # 搜索任务恢复后仍需把媒体关联传给下游导入。
                    mediaServerType=task_parameters.get("mediaServerType"),
                    mediaServerSeriesId=task_parameters.get("mediaServerSeriesId"),
                    mediaServerSeasonId=task_parameters.get("mediaServerSeasonId"),
                    mediaServerEpisodeId=task_parameters.get("mediaServerEpisodeId"),
                )

            elif task_type == "full_refresh":
                source_id = task_parameters.get("sourceId")
                if not source_id:
                    return None
                # 恢复分支按名称解析处理器，避免分支间共享局部任务模块。
                return self.task_manager.build_task_coro_factory(
                    "full_refresh", sourceId=source_id,
                    scraper_manager=scraper_manager, task_manager=self.task_manager,
                    rate_limiter=rate_limiter, metadata_manager=metadata_manager,
                    config_service=self.task_manager.config_service,
                )

            elif task_type == "refresh_episode":
                episode_id = task_parameters.get("episodeId")
                if episode_id is None:
                    return None
                return self.task_manager.build_task_coro_factory(
                    "refresh_episode", episodeId=episode_id,
                    manager=scraper_manager, rate_limiter=rate_limiter,
                    config_service=self.task_manager.config_service,
                )

            elif task_type == "incremental_refresh":
                source_id = task_parameters.get("sourceId")
                next_ep = task_parameters.get("nextEpisodeIndex")
                if not source_id or next_ep is None:
                    return None
                return self.task_manager.build_task_coro_factory(
                    "incremental_refresh",
                    sourceId=source_id,
                    nextEpisodeIndex=next_ep,
                    manager=scraper_manager,
                    task_manager=self.task_manager,
                    config_service=self.task_manager.config_service,
                    rate_limiter=rate_limiter,
                    metadata_manager=metadata_manager,
                    title_recognition_manager=title_recognition_manager,
                    animeTitle=task_parameters.get("animeTitle", ""),
                )

            elif task_type == "auto_import":
                try:
                    payload = ControlAutoImportRequest(**task_parameters)
                except Exception:
                    self.logger.warning("auto_import 任务参数解析失败，无法重建")
                    return None
                return self.task_manager.build_task_coro_factory(
                    "auto_import", payload=payload,
                    config_service=self.task_manager.config_service,
                    scraper_manager=scraper_manager,
                    metadata_manager=metadata_manager, task_manager=self.task_manager,
                    ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )

            elif task_type == "scan_and_import_target":
                provider = task_parameters.get("provider")
                external_id = task_parameters.get("externalId")
                if not provider or not external_id:
                    return None
                return self.task_manager.build_task_coro_factory(
                    "scan_and_import_target",
                    scraper_manager=scraper_manager,
                    config_service=self.task_manager.config_service,
                    provider=provider,
                    external_id=external_id,
                    title_recognition_manager=title_recognition_manager,
                    selected_episodes=task_parameters.get("selectedEpisodes"),
                )

            elif task_type in {"bangumiDataSync", "bangumiDataClear"}:
                if not task_parameters.get("manual"):
                    return None
                return self.task_manager.build_task_coro_factory(task_type)

            elif task_type == "media_scan":
                server_id = task_parameters.get("serverId")
                if server_id is None:
                    return None
                return self.task_manager.build_task_coro_factory(
                    "media_scan", server_id=server_id,
                    library_ids=task_parameters.get("libraryIds"),
                )

            elif task_type == "import_media_items":
                item_ids = task_parameters.get("itemIds")
                if not item_ids:
                    return None
                return self.task_manager.build_task_coro_factory(
                    "import_media_items", item_ids=item_ids,
                    task_manager=self.task_manager, scraper_manager=scraper_manager,
                    metadata_manager=metadata_manager,
                    config_service=self.task_manager.config_service, ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )

            elif task_type == "import_all_unimported":
                # 未导入清单由任务实时重新计算，不依赖重启前的内存状态。
                server_id = task_parameters.get("serverId")
                if not server_id:
                    return None
                return self.task_manager.build_task_coro_factory(
                    "import_all_unimported",
                    server_id=server_id,
                    media_type=task_parameters.get("mediaType"),
                    task_manager=self.task_manager,
                    scraper_manager=scraper_manager,
                    metadata_manager=metadata_manager,
                    config_service=self.task_manager.config_service,
                    ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )

            elif task_type == "manual_import":
                # XML/URL 手动导入（阶段4补齐：恢复完整性）
                source_id = task_parameters.get("sourceId")
                episode_index = task_parameters.get("episodeIndex")
                provider_name = task_parameters.get("providerName")

                if not all([source_id, episode_index, provider_name]):
                    self.logger.warning(f"manual_import 恢复失败：缺少必要参数 (sourceId/episodeIndex/providerName)")
                    return None

                # 从 source_id 反查 anime_id, title, content
                # why: 手动导入的 content（XML或URL）无法持久化到 task_parameters（可能很大），
                # 恢复时只能跳过该任务，提示用户重新提交
                self.logger.warning(
                    f"manual_import 任务无法自动恢复（content 未序列化），"
                    f"sourceId={source_id}, episodeIndex={episode_index}, providerName={provider_name}"
                )
                return None

            elif task_type == "edited_import":
                # 使用任务实际接收的编辑导入模型，避免恢复时导入不存在的名称。
                try:
                    request_data = EditedImportRequest(**task_parameters)
                except Exception as e:
                    self.logger.error(f"edited_import 恢复失败：无法解析 task_parameters: {e}")
                    return None

                return self.task_manager.build_task_coro_factory(
                    "edited_import",
                    request_data=request_data,
                    config_service=self.task_manager.config_service,
                    manager=scraper_manager,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )

            elif task_type == "download_comments":
                # 后备弹幕下载（阶段4补齐：无法恢复）
                # why: 该任务使用闭包，捕获外层运行时对象（scraper/rate_limiter/episodeId 等），
                # 这些对象无法序列化到 task_parameters，重启后无法恢复。
                # 用户可通过弹幕接口重新触发后备搜索。
                self.logger.warning(
                    f"download_comments 任务无法自动恢复（闭包依赖运行时对象），"
                    f"task_parameters={task_parameters}"
                )
                return None

            elif task_type == "match_fallback_download":
                # 匹配后备下载（B类·冷启动）——阶段5：闭包已抽取为独立可恢复任务
                # why: 参数全部可序列化存于 task_parameters，依赖（scraper/rate_limiter/config_service）
                # 在此从恢复依赖 + self.task_manager.config_service 重新注入，彻底解决原闭包无法恢复的问题。
                episode_id = task_parameters.get("episodeId")
                if not episode_id:
                    self.logger.warning("match_fallback_download 恢复失败：缺少 episodeId")
                    return None
                return self.task_manager.build_task_coro_factory(
                    "match_fallback_download",
                    episodeId=episode_id,
                    real_anime_id=task_parameters.get("real_anime_id"),
                    provider=task_parameters.get("provider"),
                    mediaId=task_parameters.get("mediaId"),
                    episode_number=task_parameters.get("episode_number"),
                    episode_title=task_parameters.get("episode_title"),
                    episode_url=task_parameters.get("episode_url"),
                    provider_episode_id=task_parameters.get("provider_episode_id"),
                    final_title=task_parameters.get("final_title"),
                    display_title=task_parameters.get("display_title"),
                    final_season=task_parameters.get("final_season"),
                    media_type=task_parameters.get("media_type"),
                    imageUrl=task_parameters.get("imageUrl"),
                    year=task_parameters.get("year"),
                    total_episodes=task_parameters.get("total_episodes"),
                    fallback_episode_cache_key=task_parameters.get("fallback_episode_cache_key"),
                    scraper_manager=scraper_manager,
                    rate_limiter=rate_limiter,
                    config_service=self.task_manager.config_service,
                )

            else:
                self.logger.warning(f"未知的任务类型 '{task_type}'，无法重建协程工厂")
                return None

        except Exception as e:
            self.logger.error(f"重建任务类型 '{task_type}' 的协程工厂时发生错误: {e}", exc_info=True)
            return None