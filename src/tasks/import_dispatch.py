"""
下载任务统一派发模块

职责：
1. 统一所有下载任务的提交入口（替代分散在各处的 submit_task 调用）
2. 标准化 task_parameters 格式（兼容现有 generic_import_task）
3. 填充 parent_task_id（搜索→下载关联）
4. 生成统一的 unique_key（基于源、目标、范围）
5. 支持方案A：冻结候选列表，下载阶段验证顺延

约束：
- 不修改 generic_import_task 的签名和实现
- 兼容现有 task_parameters 字典格式
- 只是包装提交逻辑，不改变业务流程
"""

from typing import Optional, List, Dict, Any

# 只依赖任务管理接口，具体任务实现由组合根注册。
from src.services.task_manager import TaskManager


def build_unique_key(
    provider: str,
    media_id: str,
    season: Optional[int] = None,
    selected_episodes: Optional[List[int]] = None,
    explicit_episodes: Optional[List[Dict]] = None,
    fallback_candidates: Optional[List[Dict]] = None
) -> str:
    """
    构建下载任务的幂等键

    规则：
    - 单源：provider:mediaId:season[:episodes]
    - 编辑列表：provider:mediaId:edited:hash(episodes)
    - 自动候选：auto:首选源:season（不包含候选列表，因为顺延不改变唯一性）

    Args:
        provider: 数据源
        media_id: 源站媒体ID
        season: 季度
        selected_episodes: 选定集数列表
        explicit_episodes: 用户编辑的分集列表
        fallback_candidates: 顺延候选列表（方案A）
    """
    if explicit_episodes:
        # 编辑列表：使用哈希避免键过长
        ep_indices = sorted([ep.get('episodeIndex', 0) for ep in explicit_episodes])
        ep_hash = hash(tuple(ep_indices))
        return f"{provider}:{media_id}:edited:{ep_hash}"

    if fallback_candidates:
        # 自动顺延：只用首选源，顺延是执行细节不影响唯一性
        first = fallback_candidates[0]
        first_provider = first.get('provider', provider)
        first_media_id = first.get('mediaId', media_id)
        return f"auto:{first_provider}:{first_media_id}:s{season or 1}"

    # 单源普通导入
    episodes_part = ""
    if selected_episodes:
        episodes_part = f":eps{','.join(map(str, selected_episodes))}"
    return f"{provider}:{media_id}:s{season or 1}{episodes_part}"


def build_download_parameters(
    provider: str,
    media_id: str,
    anime_title: str,
    media_type: str = "tv_series",
    season: Optional[int] = None,
    year: Optional[int] = None,
    current_episode_index: Optional[int] = None,
    image_url: Optional[str] = None,
    selected_episodes: Optional[List[int]] = None,
    explicit_episodes: Optional[List[Dict]] = None,
    fallback_candidates: Optional[List[Dict]] = None,
    # 元数据ID
    tmdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None,
    douban_id: Optional[str] = None,
    bangumi_id: Optional[str] = None,
    # 媒体服务器ID
    media_server_type: Optional[str] = None,
    media_server_series_id: Optional[str] = None,
    media_server_season_id: Optional[str] = None,
    media_server_episode_id: Optional[str] = None,
    # 后备任务标识
    is_fallback: bool = False,
    fallback_type: Optional[str] = None,
    preassigned_anime_id: Optional[int] = None,
    # 补充源
    supplement_provider: Optional[str] = None,
    supplement_media_id: Optional[str] = None,
    # 追更相关
    is_incremental_refresh: bool = False,
    incremental_refresh_source_id: Optional[int] = None,
    enable_incremental_refresh: bool = False,
) -> Dict[str, Any]:
    """
    标准化构建下载任务参数字典（兼容 generic_import_task 签名）

    统一各入口构造 task_parameters 的方式，避免字段名不一致或遗漏。
    字段名与 generic_import_task 的参数名对齐（camelCase）。

    Args:
        provider: 数据源提供商
        media_id: 媒体ID
        anime_title: 作品标题
        media_type: 媒体类型（tv_series/movie）
        season: 季度
        year: 年份
        current_episode_index: 当前集数（单集导入）
        image_url: 海报URL
        selected_episodes: 选定集数列表（整季导入）
        explicit_episodes: 显式分集列表（编辑导入）
        fallback_candidates: 后备候选列表（自动顺延，方案A）
        其余: 元数据ID、媒体服务器ID、后备标识、补充源、追更配置

    Returns:
        标准化的 task_parameters 字典（已移除 None 值）
    """
    params = {
        "provider": provider,
        "mediaId": media_id,
        "animeTitle": anime_title,
        "mediaType": media_type,
        "season": season or 1,
        "year": year,
        "currentEpisodeIndex": current_episode_index,
        "imageUrl": image_url,
        "selectedEpisodes": selected_episodes,
        "explicitEpisodes": explicit_episodes,
        "fallbackCandidates": fallback_candidates,
        # 元数据ID
        "tmdbId": tmdb_id,
        "imdbId": imdb_id,
        "tvdbId": tvdb_id,
        "doubanId": douban_id,
        "bangumiId": bangumi_id,
        # 媒体服务器ID
        "mediaServerType": media_server_type,
        "mediaServerSeriesId": media_server_series_id,
        "mediaServerSeasonId": media_server_season_id,
        "mediaServerEpisodeId": media_server_episode_id,
        # 后备任务标识
        "isFallback": is_fallback,
        "fallbackType": fallback_type,
        "preassignedAnimeId": preassigned_anime_id,
        # 补充源
        "supplementProvider": supplement_provider,
        "supplementMediaId": supplement_media_id,
        # 追更相关
        "isIncrementalRefresh": is_incremental_refresh,
        "incrementalRefreshSourceId": incremental_refresh_source_id,
        "enableIncrementalRefresh": enable_incremental_refresh,
    }

    # why: 移除 None 值，减少序列化存储空间，也让 task_parameters 更清晰
    return {k: v for k, v in params.items() if v is not None}


async def submit_fallback_download(
    task_manager: "TaskManager",
    episode_id: int,
    anime_title: str,
    season: int,
    episode_index: int,
    parent_task_id: Optional[str] = None,
) -> str:
    """
    提交后备单集下载任务（F03/F04/F18 后备下载场景）

    与普通导入的区别：
    - 已知 episode_id（数据库已有条目），不创建新条目
    - 只刷新单集弹幕，走 fallback 队列
    - 复用现有 refresh_episode_task

    Args:
        task_manager: 任务管理器
        episode_id: 数据库中的分集ID
        anime_title: 作品标题（用于任务标题显示）
        season: 季度
        episode_index: 集数
        parent_task_id: 父任务ID（后备搜索/匹配任务ID），记录派发关系

    Returns:
        task_id: 下载任务ID
    """
    # why: 幂等键基于 episode_id，同一集的重复后备下载请求会被去重复用
    unique_key = f"fallback-download:ep{episode_id}"
    task_title = f"后备下载 {anime_title} S{season}E{episode_index}"

    # 通过注册处理器消除对任务包的反向依赖，并显式注入配置服务。
    deps = task_manager.get_execution_dependencies()
    coro_factory = task_manager.build_task_coro_factory(
        "refresh_episode",
        episodeId=episode_id,
        manager=deps.get("scraper_manager"),
        rate_limiter=deps.get("rate_limiter"),
        config_service=task_manager.config_service,
    )

    task_id, _ = await task_manager.submit_task(
        coro_factory=coro_factory,
        title=task_title,
        unique_key=unique_key,
        task_type="refresh_episode",
        task_parameters={"episodeId": episode_id},
        queue_type="fallback",
        parent_task_id=parent_task_id,
    )

    return task_id


async def submit_match_fallback_download(
    task_manager: "TaskManager",
    *,
    episode_id: int,
    real_anime_id: int,
    provider: str,
    media_id: str,
    episode_number: int,
    episode_title: str,
    episode_url: str,
    provider_episode_id: str,
    final_title: str,
    display_title: str,
    final_season: int,
    media_type: str,
    image_url: Optional[str] = None,
    year: Optional[int] = None,
    total_episodes: Optional[int] = None,
    fallback_episode_cache_key: Optional[str] = None,
    notification_params: Optional[Dict[str, Any]] = None,
    parent_task_id: Optional[str] = None,
) -> tuple:
    """提交"冷启动"匹配后备下载任务（B类：库中无条目，需全链路现建）。

    与 submit_fallback_download（A类：已入库单集补弹幕，走 refresh_episode_task）不同，
    本入口对应 comments.py 的匹配后备闭包逻辑：下载→冷启动建库→入库→写3种缓存。

    依赖注入：从 task_manager.get_execution_dependencies() 取 scraper_manager/rate_limiter，
    从 task_manager.config_service 取 config_service，通过 coro_factory 注入到任务函数。
    task_parameters 只存可序列化参数，供任务恢复时由 _rebuild_coro_factory 重建。

    Args:
        episode_id: 14位真实 episodeId
        real_anime_id: 真实 animeId（作品主键）
        display_title: 建 Anime 用的展示标题
        final_title: match_season 缓存键纯标题解析基准
        total_episodes: 整部剧集数（有则写整季基准缓存）
        fallback_episode_cache_key: 待清理的 fallback_search 旧缓存键（不含前缀）
        notification_params: 完成通知渲染参数（anime_title/season/episode/provider/imageUrl/
            is_movie/media_type），合并进 task_parameters 供通知链路读取；恢复时忽略这些字段
        parent_task_id: 父任务ID（匹配后备任务ID），记录派发关系

    Returns:
        (task_id, done_event): 任务ID 与完成事件（供调用方 await 等待/注册回调）
    """
    # why: 幂等键基于 episodeId，同一集的重复冷启动下载请求被去重复用
    unique_key = f"match_fallback_comments_{episode_id}"
    task_title = f"匹配后备下载 {display_title} S{final_season}E{episode_number} [{provider}]"

    # 动态导入避免循环依赖

    deps = task_manager.get_execution_dependencies()
    scraper_manager = deps.get("scraper_manager")
    rate_limiter = deps.get("rate_limiter")
    config_service = getattr(task_manager, "config_service", None)

    # 可序列化参数（供任务恢复；不含依赖对象）
    task_parameters = {
        "episodeId": episode_id,
        "real_anime_id": real_anime_id,
        "provider": provider,
        "mediaId": media_id,
        "episode_number": episode_number,
        "episode_title": episode_title,
        "episode_url": episode_url,
        "provider_episode_id": provider_episode_id,
        "final_title": final_title,
        "display_title": display_title,
        "final_season": final_season,
        "media_type": media_type,
        "imageUrl": image_url,
        "year": year,
        "total_episodes": total_episodes,
        "fallback_episode_cache_key": fallback_episode_cache_key,
    }
    # 合并通知参数（供完成通知渲染；恢复时 _rebuild_coro_factory 只取下载字段，忽略这些）
    if notification_params:
        task_parameters.update(notification_params)

    # 首次派发与恢复使用同一注册处理器，避免包初始化依赖。
    coro_factory = task_manager.build_task_coro_factory(
        "match_fallback_download",
        episodeId=episode_id,
        real_anime_id=real_anime_id,
        provider=provider,
        mediaId=media_id,
        episode_number=episode_number,
        episode_title=episode_title,
        episode_url=episode_url,
        provider_episode_id=provider_episode_id,
        final_title=final_title,
        display_title=display_title,
        final_season=final_season,
        media_type=media_type,
        imageUrl=image_url,
        year=year,
        total_episodes=total_episodes,
        fallback_episode_cache_key=fallback_episode_cache_key,
        scraper_manager=scraper_manager,
        rate_limiter=rate_limiter,
        config_service=config_service,
    )

    task_id, done_event = await task_manager.submit_task(
        coro_factory=coro_factory,
        title=task_title,
        unique_key=unique_key,
        task_type="match_fallback_download",
        task_parameters=task_parameters,
        queue_type="fallback",
        parent_task_id=parent_task_id,
    )

    return task_id, done_event


async def submit_import_task(
    task_manager: "TaskManager",
    task_parameters: Dict[str, Any],
    task_title: Optional[str] = None,
    parent_task_id: Optional[str] = None,
    queue_type: str = "download",
) -> str:
    """
    统一提交下载任务（兼容现有 task_parameters 格式）

    Args:
        task_manager: 任务管理器
        task_parameters: 任务参数字典（兼容 generic_import_task）
        task_title: 任务标题（可选，自动生成）
        parent_task_id: 父任务ID（搜索任务ID），填充到 task_history.parent_task_id
        queue_type: 队列类型（默认 download）

    Returns:
        task_id: 下载任务ID
    """
    # 自动生成 unique_key（如果未提供）
    provider = task_parameters.get("provider")
    media_id = task_parameters.get("mediaId")
    season = task_parameters.get("season")
    selected_episodes = task_parameters.get("selectedEpisodes")
    explicit_episodes = task_parameters.get("explicitEpisodes")
    fallback_candidates = task_parameters.get("fallbackCandidates")

    # 单集任务也参与范围去重，但不改动导入参数本身，避免改变反向偏移语义。
    key_episodes = selected_episodes
    if key_episodes is None and task_parameters.get("currentEpisodeIndex") is not None:
        key_episodes = [task_parameters["currentEpisodeIndex"]]
    unique_key = build_unique_key(
        provider=provider,
        media_id=media_id,
        season=season,
        selected_episodes=key_episodes,
        explicit_episodes=explicit_episodes,
        fallback_candidates=fallback_candidates
    )

    # 自动生成任务标题（如果未提供）
    if not task_title:
        anime_title = task_parameters.get("animeTitle", "未知作品")
        episodes_desc = ""
        if explicit_episodes:
            episodes_desc = f" ({len(explicit_episodes)}集)"
        elif selected_episodes:
            if len(selected_episodes) == 1:
                episodes_desc = f" 第{selected_episodes[0]}集"
            else:
                episodes_desc = f" ({len(selected_episodes)}集)"
        task_title = f"导入 {anime_title} S{season or 1}{episodes_desc}"

    # 按注册名称构建工厂，派发模块不再通过任务包查找通用导入实现。
    deps = task_manager.get_execution_dependencies()
    coro_factory = task_manager.build_task_coro_factory(
        "generic_import",
        provider=task_parameters.get("provider"),
        mediaId=task_parameters.get("mediaId"),
        animeTitle=task_parameters.get("animeTitle"),
        mediaType=task_parameters.get("mediaType"),
        season=task_parameters.get("season"),
        year=task_parameters.get("year"),
        currentEpisodeIndex=task_parameters.get("currentEpisodeIndex"),
        imageUrl=task_parameters.get("imageUrl"),
        config_service=task_manager.config_service,
        metadata_manager=deps.get("metadata_manager"),
        manager=deps.get("scraper_manager"),
        task_manager=task_manager,
        rate_limiter=deps.get("rate_limiter"),
        title_recognition_manager=deps.get("title_recognition_manager"),
        doubanId=task_parameters.get("doubanId"),
        tmdbId=task_parameters.get("tmdbId"),
        imdbId=task_parameters.get("imdbId"),
        tvdbId=task_parameters.get("tvdbId"),
        bangumiId=task_parameters.get("bangumiId"),
        selectedEpisodes=task_parameters.get("selectedEpisodes"),
        # 媒体关联字段必须进入实际执行工厂，不能只保存在通知参数中。
        mediaServerType=task_parameters.get("mediaServerType"),
        mediaServerSeriesId=task_parameters.get("mediaServerSeriesId"),
        mediaServerSeasonId=task_parameters.get("mediaServerSeasonId"),
        mediaServerEpisodeId=task_parameters.get("mediaServerEpisodeId"),
        fallbackCandidates=task_parameters.get("fallbackCandidates"),
        # 派发层必须将持久化的追更意图传给实际执行任务。
        enable_incremental_refresh=bool(task_parameters.get("enableIncrementalRefresh", False)),
    )

    # 提交任务
    task_id, _ = await task_manager.submit_task(
        coro_factory=coro_factory,
        title=task_title,
        unique_key=unique_key,
        task_type="generic_import",
        task_parameters=task_parameters,
        queue_type=queue_type,
        parent_task_id=parent_task_id,
    )

    return task_id
