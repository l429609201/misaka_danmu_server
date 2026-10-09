"""任务结果到用户通知事件的业务映射及作品信息补全。"""

import datetime
import logging
import re
from typing import Any, Dict, Optional, Tuple

from src.services.task_manager import Task

logger = logging.getLogger(__name__)

def _parse_import_unique_key(key: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """从导入类 unique_key 解析出 (provider, media_id, season)。

    支持的格式：
      - import-{provider}-{mediaId}-S{season}-ep{ep}
      - import-{provider}-{mediaId}-{8位hash}
      - ui-import-{provider}-{mediaId}-season-{s}-episode-{e}-{type}
      - url-import-{provider}-{mediaId}-{type}-season-{s} / url-import-{provider}-{mediaId}-{type}

    why：mediaId 可能含连字符，故不能简单 split。先剥离已知前缀，再从尾部
    剥离已知后缀标记（-S{n}-ep{m} / -season-{n}[-...] / 末尾8位hash），
    剩余部分首段为 provider、其余为 mediaId。解析失败返回 None（调用方静默跳过）。
    """
    if not key:
        return None
    # 剥离前缀
    prefix = None
    for p in ("ui-import-", "url-import-", "import-"):
        if key.startswith(p):
            prefix = p
            break
    if prefix is None:
        return None
    body = key[len(prefix):]

    season: Optional[int] = None
    # 剥离 -S{season}-ep{ep} 尾部（webhook 导入）
    m = re.search(r"-S(\d+)-ep\d*$", body)
    if m:
        season = int(m.group(1))
        body = body[:m.start()]
    else:
        # 剥离 -season-{s}[-episode-{e}][-{type}] 尾部（ui-import / url-import）
        m2 = re.search(r"-season-(\d+)(?:-.*)?$", body)
        if m2:
            season = int(m2.group(1))
            body = body[:m2.start()]
        else:
            # 剥离末尾 8 位 hash（编辑导入 import-{provider}-{mediaId}-{hash}）
            m3 = re.search(r"-[0-9a-f]{8}$", body)
            if m3:
                body = body[:m3.start()]
            else:
                # url-import-{provider}-{mediaId}-{type}：剥离末尾已知类型
                m4 = re.search(r"-(movie|tv_series|tv|other)$", body)
                if m4:
                    body = body[:m4.start()]

    parts = body.split("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    provider, media_id = parts[0], parts[1]
    return provider, media_id, season


class TaskNotificationWorkflow:
    """识别任务业务事件并通过通知流程发送。"""

    def __init__(self, database_service, notifications) -> None:
        self._db = database_service
        self.notifications = notifications
        self.logger = logger

    async def emit_progress(self, task: Task, progress: int, description: str) -> None:
        """保持内部健康任务静默，普通任务按渠道编辑能力输出进度。"""
        if self.determine_event_type(task, True) is not None:
            await self.notifications.emit_task_progress(task.task_id, task.title, progress, description)

    def determine_event_type(self, task: Task, is_success: bool) -> Optional[str]:
        """根据任务的 unique_key 和 title 判断应触发的通知事件类型"""
        key = task.unique_key or ""
        title = task.title or ""
        suffix = "_success" if is_success else "_failed"

        # 内部健康统计保留任务历史，但不发送用户通知。
        if task.task_type == "search_health_update":
            return None

        # 删除任务不发通知（必须覆盖所有删除前缀，否则会掉到末尾兜底被误判为 import_success，
        # 导致出现"? 导入成功 / 删除成功"这种标题与内容矛盾的通知）
        if key.startswith((
            "delete-source-",        # 删除单个数据源
            "delete-bulk-sources-",  # 批量删除数据源
            "delete-anime-",         # 删除作品
            "delete-episode-",       # 删除单个分集
            "delete-bulk-episodes-", # 批量删除分集
            "modify-episodes-",      # 集数偏移（管理类操作，非导入，避免误判为"导入成功"）
        )):
            return None

        # 定时任务（有 scheduled_task_id）
        if task.scheduled_task_id:
            return "scheduled_task_complete" if is_success else "scheduled_task_failed"

        # Webhook 导入
        if key.startswith("webhook-search-"):
            return f"webhook_import{suffix}"

        # 数据源刷新（含定时"刷新最新集" refresh-latest- 前缀，否则会掉到末尾兜底被误判为导入）
        if (key.startswith("refresh-episode-") or key.startswith("full-refresh-")
                or key.startswith("bulk-refresh-") or key.startswith("refresh-latest-")):
            return f"refresh{suffix}"

        # 追更刷新（增量刷新 job 提交的导入任务，通过 title 识别）
        if "追更" in title or "增量刷新" in title:
            return f"incremental_refresh{suffix}"

        # 自动导入
        if key.startswith("auto-import-"):
            return f"auto_import{suffix}"

        # 媒体库扫描
        if key.startswith("scan-media-server-"):
            return "media_scan_complete" if is_success else None

        # 通用导入（UI导入、URL导入、手动导入、批量导入、编辑后导入等）
        if key.startswith(("ui-import-", "url-import-", "manual-import-", "batch-manual-import-", "import-")):
            return f"import{suffix}"

        # 后备下载/搜索任务 —— 按 title 前缀细分，与 _get_progress_callback 的
        # _FALLBACK_PROGRESS_KEY_MAP 保持一致，确保进度和完成通知使用相同的订阅 key
        if getattr(task, "queue_type", "") == "fallback":
            _FALLBACK_EVENT_MAP = {
                "后备搜索:": "fallback_search",
                "预下载弹幕:": "predownload",
                "后备匹配:": "match_fallback",
            }
            prefix = next(
                (v for k, v in _FALLBACK_EVENT_MAP.items() if title.startswith(k)),
                "download_fallback",  # 兜底
            )
            return f"{prefix}{suffix}"

        # 兜底：有 unique_key 但未匹配到的，按导入处理
        if key:
            return f"import{suffix}"

        return None

    async def emit_task_event(self, task: Task, is_success: bool, message: str = ""):
        """发射任务完成/失败的通知事件"""
        if not self.notifications:
            return
        event_type = self.determine_event_type(task, is_success)
        if not event_type:
            # 即使不需要发通知，也要清理进度消息缓存
            self.notifications.state.cleanup_task_progress(task.task_id)
            return

        # 1. 优先从 task_parameters 取 imageUrl（import/auto_import 任务已有）
        image_url: str = (task.task_parameters or {}).get("imageUrl", "") or ""

        # 2. 刷新类任务 task_parameters 通常缺标题/集数/年份/海报，从数据库补查。
        #    db_extra 收集补查到的字段，稍后仅用于填补 payload 中为空的项（不覆盖已有值）。
        db_extra: Dict[str, Any] = {}
        if task.unique_key:
            key = task.unique_key
            try:
                async with self._db.transaction():
                    if key.startswith("refresh-episode-"):
                        # refresh-episode-{episodeId}：第三段是 episodeId
                        try:
                            episode_id = int(key.split("-")[2])
                        except (ValueError, IndexError):
                            episode_id = None
                        if episode_id is not None:
                            ep_info = await self._db.episode.get_episode_provider_info(episode_id)
                            if ep_info:
                                db_extra["episode"] = ep_info.get("episodeIndex")
                                # 通过 animeId 反查 Anime 标题/年份/季/海报
                                anime_row = await self._db.anime.get_by_id(ep_info.get("animeId"))
                                if anime_row:
                                    db_extra["anime_title"] = anime_row.title
                                    db_extra["season"] = anime_row.season
                                    db_extra["year"] = anime_row.year
                                    db_extra["image_url"] = (anime_row.localImagePath or anime_row.imageUrl or "")
                                db_extra["source"] = ep_info.get("providerName", "")
                    elif key.startswith("refresh-latest-"):
                        # refresh-latest-{sourceId}-ep{n}：第三段是 sourceId，ep 后是集号（非 episodeId）。
                        # 按 sourceId 反查作品/源信息，集号从 -ep 后解析。
                        source_id = None
                        ep_index = None
                        try:
                            rest = key[len("refresh-latest-"):]
                            sid_part, _, ep_part = rest.partition("-ep")
                            source_id = int(sid_part)
                            if ep_part:
                                ep_index = int(ep_part)
                        except (ValueError, IndexError):
                            pass
                        if source_id is not None:
                            info = await self._db.source.get_anime_source_info(source_id)
                            if info:
                                db_extra["anime_title"] = info.get("title", "")
                                db_extra["season"] = info.get("season")
                                db_extra["year"] = info.get("year")
                                db_extra["source"] = info.get("providerName", "")
                                db_extra["image_url"] = info.get("localImagePath") or info.get("imageUrl", "") or ""
                                if ep_index is not None:
                                    db_extra["episode"] = ep_index
                    elif key.startswith("full-refresh-") or key.startswith("bulk-refresh-"):
                        # full-refresh-{anime_id}-xxx：直接查 Anime
                        try:
                            anime_id = int(key.split("-")[2])
                        except (ValueError, IndexError):
                            anime_id = None
                        if anime_id is not None:
                            anime_row = await self._db.anime.get_by_id(anime_id)
                            if anime_row:
                                db_extra["anime_title"] = anime_row.title
                                db_extra["season"] = anime_row.season
                                db_extra["year"] = anime_row.year
                                db_extra["image_url"] = (anime_row.localImagePath or anime_row.imageUrl or "")
            except Exception:
                pass  # 补查失败不影响通知发出

        # 3. 兜底：task_parameters 带 sourceId 的刷新类任务（如 TG 刷新 tg_refresh、指令刷新），
        #    其 unique_key 无 refresh 前缀，上面补不到，这里按 sourceId 反查作品/源信息。
        if not db_extra.get("anime_title"):
            source_id = (task.task_parameters or {}).get("sourceId")
            if source_id is not None:
                try:
                    async with self._db.transaction():
                        info = await self._db.source.get_anime_source_info(int(source_id))
                        if info:
                            db_extra["anime_title"] = info.get("title", "")
                            db_extra["season"] = info.get("season")
                            db_extra["year"] = info.get("year")
                            db_extra["source"] = info.get("providerName", "")
                            db_extra["tmdb_id"] = info.get("tmdbId", "") or ""
                            if not db_extra.get("image_url"):
                                db_extra["image_url"] = info.get("localImagePath") or info.get("imageUrl", "") or ""
                except Exception:
                    pass  # 补查失败不影响通知发出

        # 4. 兜底：导入类任务（import-/ui-import-/url-import- 前缀）若 task_parameters
        #    未带 animeTitle（如 direct_import / URL导入 / 旧路径），从 unique_key 解析
        #    provider+mediaId 反查 DB 补齐作品名/季/来源。why：媒体库逐集导入等路径
        #    的微信通知只剩弹幕数，看不出是哪部作品。
        if not db_extra.get("anime_title") and not (task.task_parameters or {}).get("animeTitle"):
            key = task.unique_key or ""
            parsed = _parse_import_unique_key(key)
            if parsed:
                provider, media_id, season_hint = parsed
                try:
                    async with self._db.transaction():
                        anime_id = await self._db.source.get_anime_id_by_source_media_id(
                            provider, media_id, season=season_hint
                        )
                        # season 提示查不到时退化为不带 season 再查一次
                        if anime_id is None and season_hint is not None:
                            anime_id = await self._db.source.get_anime_id_by_source_media_id(
                                provider, media_id
                            )
                        if anime_id is not None:
                            anime_row = await self._db.anime.get_by_id(anime_id)
                            if anime_row:
                                db_extra["anime_title"] = anime_row.title
                                db_extra["season"] = anime_row.season
                                db_extra["year"] = anime_row.year
                                db_extra["media_type"] = anime_row.type
                                if not db_extra.get("image_url"):
                                    db_extra["image_url"] = (anime_row.localImagePath or anime_row.imageUrl or "")
                            db_extra["source"] = provider
                except Exception:
                    pass  # 补查失败不影响通知发出

        # 已本地化的海报优先，避免把原始外链重新交给渠道；无本地文件时才使用任务参数。
        local_image_url = db_extra.get("image_url", "") or ""
        if local_image_url:
            image_url = local_image_url

        try:
            params = task.task_parameters or {}
            # 提取任务参数中的上下文字段，供通知格式化使用
            extra = {
                "search_term": params.get("searchTerm", ""),
                "search_type": str(params.get("searchType", "")).replace("AutoImportSearchType.", "").lower(),
                "season": params.get("season"),
                "episode": params.get("episode") if params.get("episode") is not None else params.get("currentEpisodeIndex", params.get("episodeIndex", params.get("episode_number"))),
                "episode_range": params.get("episodeRange", params.get("episode_range")),
                "anime_title": params.get("animeTitle", "") or params.get("anime_title", ""),
                "episode_count": params.get("episodeCount"),
                "webhook_source": params.get("webhookSource", ""),
                "provider": params.get("provider", "") or params.get("providerName", ""),
                # source 字段：新消息类导入模板读取 source 展示"来源/弹幕源"，映射自 provider
                "source": params.get("provider", "") or params.get("providerName", ""),
                "media_id": params.get("mediaId", "") or params.get("media_id", ""),
                "tmdb_id": params.get("tmdbId", ""),
                "media_type": params.get("type", "") or params.get("mediaType", ""),
                "year": params.get("year"),
                "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            # 用数据库补查结果填补 extra 中为空的字段（不覆盖任务参数已有的值）。
            # 这样刷新类任务也能在通知里显示标题/集数/年份/季/海报。
            for _k, _v in db_extra.items():
                if _v in (None, "") :
                    continue
                if extra.get(_k) in (None, "", 0):
                    extra[_k] = _v
            await self.notifications.emit_event(event_type, {
                "task_title": task.title,
                "message": message,
                "task_id": task.task_id,
                "unique_key": task.unique_key or "",
                "image_url": image_url,
                "task_parameters": params,
                **extra,
            })
        except Exception as e:
            self.logger.error(f"发射通知事件 {event_type} 失败: {e}")

