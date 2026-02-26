import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select
import httpx

from src.db import crud, models
from src.services.alias_service import extract_aliases_from_details, validate_aliases_with_ai
from .base import BaseJob
from src.rate_limiter import RateLimiter
from src.services import TaskManager, TaskSuccess, ScraperManager, MetadataSourceManager
from src.ai import AIMatcherManager
from src.ai.ai_prompts import DEFAULT_AI_MATCH_PROMPT, DEFAULT_AI_RECOGNITION_PROMPT, DEFAULT_AI_ALIAS_VALIDATION_PROMPT

class TmdbAutoMapJob(BaseJob):
    job_type = "tmdbAutoScrape"
    job_name = "TMDB自动刮削与剧集组映射"
    description = "自动从TMDB刮削已导入作品的别名、剧集组信息，更新分集映射关系。帮助解决分集顺序不一致的问题。"
    config_schema = [
        {
            "key": "forceScrape",
            "label": "强制刮削",
            "type": "boolean",
            "description": "关闭时，已有剧集组映射的条目将被跳过；开启时，强制覆盖所有条目的刮削数据",
            "default": False,
        },
        {
            "key": "enableEpisodeGroup",
            "label": "剧集组刮削",
            "type": "boolean",
            "description": "开启后将从TMDB获取剧集组信息并更新分集映射关系；关闭时仅刮削别名信息",
            "default": False,
        },
    ]

    # 修正：此任务不涉及弹幕下载，因此移除不必要的 rate_limiter 依赖
    # 修正：接收正确的依赖项
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], task_manager: TaskManager, metadata_manager: MetadataSourceManager, ai_matcher_manager: AIMatcherManager):
        # 由于此任务的依赖项与基类不同，我们不调用 super().__init__，
        # 而是直接初始化此任务所需的属性。
        self._session_factory = session_factory
        self.task_manager = task_manager
        self.metadata_manager = metadata_manager
        self.ai_matcher_manager = ai_matcher_manager
        self.logger = logging.getLogger(self.__class__.__name__)


    async def run(self, session: AsyncSession, progress_callback: Callable, task_config: dict = None):
        """
        定时任务的核心逻辑。
        1. 获取所有TV系列作品
        2. 对于没有TMDB ID的作品，通过标题搜索TMDB获取ID
        3. 刮削别名信息
        4. 获取并映射剧集组信息

        Args:
            task_config: 任务实例级配置字典，包含 forceScrape 等选项。
        """
        if task_config is None:
            task_config = {}
        force_scrape = task_config.get("forceScrape", False)
        enable_episode_group = task_config.get("enableEpisodeGroup", False)
        self.logger.info(f"开始执行 [{self.job_name}] 定时任务... (强制刮削: {'开启' if force_scrape else '关闭'}, 剧集组刮削: {'开启' if enable_episode_group else '关闭'})")
        await progress_callback(0, "正在初始化...")

        # 为元数据管理器调用创建一个虚拟用户对象
        user = models.User(id=0, username="scheduled_task")

        # 【性能优化】AI初始化预热：如果AI已启用，提前开始初始化（不阻塞）
        ai_matcher_warmup_task = None
        ai_matcher = None
        ai_recognition_enabled = False
        ai_alias_correction_enabled = False
        try:
            ai_match_enabled = await self.ai_matcher_manager.is_enabled()
            ai_recognition_enabled = await crud.get_config_value(session, "aiRecognitionEnabled", "false") == "true"
            ai_alias_correction_enabled = await crud.get_config_value(session, "aiAliasCorrectionEnabled", "false") == "true"

            if ai_match_enabled and ai_recognition_enabled:
                self.logger.info("AI辅助识别已启用")
                if ai_alias_correction_enabled:
                    self.logger.info("AI别名修正已启用")

                # 动态注册AI提示词配置(如果不存在则创建,使用硬编码默认值)
                await crud.initialize_configs(session, {
                    "aiMatchPrompt": (DEFAULT_AI_MATCH_PROMPT, "AI智能匹配提示词"),
                    "aiRecognitionPrompt": (DEFAULT_AI_RECOGNITION_PROMPT, "AI辅助识别提示词"),
                    "aiAliasValidationPrompt": (DEFAULT_AI_ALIAS_VALIDATION_PROMPT, "AI别名验证提示词")
                })

                # 【性能优化】启动AI匹配器预热任务（并行）
                ai_matcher_warmup_task = asyncio.create_task(self.ai_matcher_manager.get_matcher())
                self.logger.debug("TMDB自动映射 AI匹配器预热已启动（并行）")
        except Exception as e:
            self.logger.warning(f"初始化AI matcher失败: {e}, 将使用传统搜索")

        # 获取所有作品(TV系列和电影/剧场版)
        from src.db.orm_models import Anime, AnimeMetadata
        stmt = (
            select(
                Anime.id.label("animeId"),
                Anime.title,
                Anime.year,
                Anime.type,
                AnimeMetadata.tmdbId,
                AnimeMetadata.tmdbEpisodeGroupId
            )
            .outerjoin(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
            .where(Anime.type.in_(['tv_series', 'movie']))
        )
        result = await session.execute(stmt)
        shows_to_update = [dict(row) for row in result.mappings()]

        total_shows = len(shows_to_update)
        self.logger.info(f"找到 {total_shows} 个作品需要处理(TV系列和电影)。")
        await progress_callback(5, f"找到 {total_shows} 个作品待处理")

        processed_count = 0
        scraped_count = 0
        mapped_count = 0
        skipped_count = 0

        for i, show in enumerate(shows_to_update):
            current_progress = 5 + int((i / total_shows) * 95) if total_shows > 0 else 95
            anime_id = show['animeId']
            title = show['title']
            year = show.get('year')
            tmdb_id = show.get('tmdbId')

            await progress_callback(current_progress, f"正在处理: {title} ({i+1}/{total_shows})")
            self.logger.info(f"正在处理: '{title}' (Anime ID: {anime_id}, TMDB ID: {tmdb_id or '无'})")

            try:
                # 非强制刮削模式下，跳过已有完整数据的条目
                existing_group_id = show.get('tmdbEpisodeGroupId')
                if not force_scrape and tmdb_id and existing_group_id:
                    self.logger.info(f"跳过 '{title}': 已有TMDB ID({tmdb_id})和剧集组({existing_group_id})，非强制刮削模式。")
                    skipped_count += 1
                    processed_count += 1
                    continue

                # 初始化变量
                use_episode_group = False
                recognized_season = None
                search_type = show.get('type', 'tv_series')  # 提前初始化,供AI验证别名使用

                # 步骤 1: 如果没有TMDB ID，尝试通过标题搜索获取
                if not tmdb_id:
                    self.logger.info(f"'{title}' 没有TMDB ID，尝试搜索...")
                    try:
                        # 使用AI标准化标题 (如果启用)
                        search_title = title
                        search_year = year

                        # 【性能优化】第一次使用AI时，await预热task
                        if ai_matcher_warmup_task and not ai_matcher:
                            ai_matcher = await ai_matcher_warmup_task
                            ai_matcher_warmup_task = None  # 清空task，避免重复await
                            if not ai_matcher:
                                self.logger.warning("AI匹配器初始化失败,将使用传统搜索")

                        if ai_matcher and ai_recognition_enabled:
                            try:
                                recognition_result = await ai_matcher.recognize_title(
                                    title=title,
                                    year=year,
                                    anime_type=search_type
                                )
                                if recognition_result:
                                    search_title = recognition_result.get("search_title", title)
                                    search_year = recognition_result.get("year", year)
                                    search_type = recognition_result.get("type", search_type)
                                    use_episode_group = recognition_result.get("use_episode_group", False)
                                    recognized_season = recognition_result.get("season")

                                    if recognized_season:
                                        self.logger.info(f"AI识别到季度: {recognized_season}")
                                        # 如果识别到季度信息，去除标题中的季度后缀
                                        # 匹配 "第X季", "第X期", "Season X", "S X" 等格式
                                        season_pattern = r'\s*(?:第[0-9一二三四五六七八九十百千万]+[季期]|Season\s*\d+|S\s*\d+)\s*$'
                                        cleaned_title = re.sub(season_pattern, '', search_title, flags=re.IGNORECASE)
                                        if cleaned_title != search_title:
                                            self.logger.info(f"去除季度后缀: '{search_title}' → '{cleaned_title}'")
                                            search_title = cleaned_title

                                    if use_episode_group:
                                        self.logger.info(f"AI识别: 该作品需要使用剧集组")

                                    self.logger.info(f"AI标准化: '{title}' → '{search_title}' (year={search_year}, type={search_type})")
                            except Exception as e:
                                self.logger.warning(f"AI标准化失败: {e}, 使用原标题搜索")

                        # 后备：如果AI未启用或AI未识别到季度，尝试用正则提取季度信息并清理标题
                        if recognized_season is None:
                            from src.utils import parse_search_keyword
                            parsed = parse_search_keyword(search_title)
                            if parsed.get("season") is not None:
                                recognized_season = parsed["season"]
                                search_title = parsed["title"]
                                self.logger.info(f"正则提取季度: '{title}' → '{search_title}' (season={recognized_season})")

                        # 根据类型选择mediaType
                        media_type = "movie" if search_type == "movie" else "tv"

                        search_results = await self.metadata_manager.search("tmdb", search_title, user, mediaType=media_type)
                        if search_results:
                            # 智能选择最佳匹配结果
                            best_match = None

                            # 如果AI识别到季度信息,说明肯定是TV类型
                            if recognized_season is not None and media_type == "tv":
                                self.logger.info(f"检测到季度信息(season={recognized_season}),强制使用TV类型筛选")
                                # 筛选TV类型的结果
                                tv_results = [r for r in search_results if r.type == "tv" or not r.type]
                                if tv_results:
                                    search_results = tv_results
                                    self.logger.info(f"筛选后剩余 {len(tv_results)} 个TV类型结果")

                            # 如果有多个结果且启用了AI匹配，使用AI智能选择
                            if ai_matcher and ai_recognition_enabled and len(search_results) > 1:
                                self.logger.info(f"TMDB搜索返回 {len(search_results)} 个结果，使用AI智能匹配...")

                                # 转换MetadataDetailsResponse为ProviderSearchInfo格式供AI使用
                                provider_results = []
                                for r in search_results:
                                    provider_results.append(models.ProviderSearchInfo(
                                        provider="tmdb",
                                        mediaId=r.tmdbId or r.id,
                                        title=r.title,
                                        type=r.type or "unknown",
                                        season=1,
                                        year=r.year,
                                        imageUrl=r.imageUrl,
                                        episodeCount=None
                                    ))

                                # 构建查询信息
                                query_info = {
                                    "title": title,  # 使用原始标题（包含季度信息）
                                    "year": year,
                                    "type": search_type
                                }

                                try:
                                    # 调用AI匹配
                                    best_index = await ai_matcher.select_best_match(query_info, provider_results, favorited_info={})
                                    if best_index is not None:
                                        best_match = search_results[best_index]
                                        self.logger.info(f"AI选择了结果 #{best_index}: {best_match.title} ({best_match.year})")
                                    else:
                                        self.logger.warning("AI未能选择合适的匹配，使用第一个结果")
                                        best_match = search_results[0]
                                except Exception as e:
                                    self.logger.warning(f"AI匹配失败: {e}，使用第一个结果")
                                    best_match = search_results[0]
                            else:
                                # 没有AI或只有一个结果，使用传统匹配
                                best_match = search_results[0]

                                # 年份匹配
                                if search_year and len(search_results) > 1:
                                    # 尝试找到年份匹配的结果
                                    for result in search_results:
                                        if result.year == search_year:
                                            best_match = result
                                            self.logger.info(f"找到年份匹配的结果: {result.title} ({result.year})")
                                            break

                            tmdb_id = best_match.tmdbId or best_match.id
                            self.logger.info(f"为 '{title}' 找到TMDB ID: {tmdb_id} (类型: {best_match.type or 'unknown'})")

                            # 保存TMDB ID到数据库
                            await crud.update_metadata_if_empty(
                                session,
                                anime_id,
                                tmdb_id=tmdb_id
                            )
                            await session.commit()
                            scraped_count += 1
                        else:
                            # 回退策略: 如果检测到季度信息，尝试从数据库中同系列作品继承TMDB ID
                            if recognized_season is not None:
                                base_title = search_title  # 已被 parse_search_keyword 或 AI 清理过的基础标题
                                self.logger.info(f"TMDB搜索无结果，尝试从同系列作品继承TMDB ID (基础标题: '{base_title}')")
                                sibling_stmt = (
                                    select(AnimeMetadata.tmdbId)
                                    .join(Anime, Anime.id == AnimeMetadata.animeId)
                                    .where(
                                        Anime.title.like(f"{base_title}%"),
                                        AnimeMetadata.tmdbId.isnot(None),
                                        AnimeMetadata.tmdbId != "",
                                        Anime.type == 'tv_series'
                                    )
                                    .limit(1)
                                )
                                sibling_result = await session.execute(sibling_stmt)
                                inherited_tmdb_id = sibling_result.scalar_one_or_none()

                                if inherited_tmdb_id:
                                    tmdb_id = inherited_tmdb_id
                                    self.logger.info(f"从同系列作品继承TMDB ID: '{title}' → TMDB ID: {tmdb_id}")
                                    await crud.update_metadata_if_empty(session, anime_id, tmdb_id=tmdb_id)
                                    await session.commit()
                                    scraped_count += 1
                                else:
                                    self.logger.warning(f"未能为 '{title}' 找到TMDB搜索结果，也未找到同系列作品的TMDB ID。")
                                    continue
                            else:
                                self.logger.warning(f"未能为 '{title}' 找到TMDB搜索结果。")
                                continue
                    except Exception as e:
                        self.logger.error(f"搜索 '{title}' 时发生错误: {e}")
                        continue

                # 步骤 1.5: 对于已有TMDB ID的作品，也需要识别季度信息
                # （搜索分支内的AI识别和正则提取只在没有TMDB ID时执行）
                if recognized_season is None:
                    from src.utils import parse_search_keyword
                    parsed = parse_search_keyword(title)
                    if parsed.get("season") is not None:
                        recognized_season = parsed["season"]
                        self.logger.info(f"正则提取季度(已有TMDB ID): '{title}' → season={recognized_season}")

                # 步骤 2: 获取媒体详情，包括别名
                # 根据作品类型决定mediaType参数
                media_type_for_details = "movie" if search_type == "movie" else "tv"
                details = await self.metadata_manager.get_details("tmdb", tmdb_id, user, mediaType=media_type_for_details)
                if not details:
                    self.logger.warning(f"未能从 TMDB 获取 '{title}' (ID: {tmdb_id}) 的详情 (mediaType={media_type_for_details})。")
                    continue

                # 步骤 3: 准备别名（暂不更新到数据库，等待剧集组识别后可能需要追加季度后缀）
                aliases_to_update = extract_aliases_from_details(details)
                force_update = False

                if ai_matcher and ai_recognition_enabled and search_type == "tv_series" and aliases_to_update:
                    aliases_to_update, force_update = await validate_aliases_with_ai(
                        title, year, search_type, aliases_to_update, ai_matcher, ai_alias_correction_enabled
                    )

                # 步骤 3.5: 如果识别到季度>=2，为中文别名追加季度后缀
                # 无论是否开启剧集组刮削，都需要确保别名与标题中的季度信息一致
                if recognized_season is not None and recognized_season >= 2 and aliases_to_update:
                    season_suffix = f" 第{recognized_season}季"
                    if aliases_to_update.get("aliases_cn"):
                        updated_cn_aliases = []
                        for cn_alias in aliases_to_update["aliases_cn"]:
                            if cn_alias and not cn_alias.endswith(season_suffix):
                                updated_cn_aliases.append(cn_alias + season_suffix)
                            else:
                                updated_cn_aliases.append(cn_alias)
                        aliases_to_update["aliases_cn"] = updated_cn_aliases
                        self.logger.info(f"为 '{title}' 的中文别名追加季度后缀: {season_suffix}")

                # 步骤 4: 智能季度匹配 - 两级查找逻辑 (仅适用于TV系列)
                # 第一级: 使用seasons信息进行匹配(方案A)
                # 第二级: 使用"Seasons"剧集组进行匹配(方案C)

                # 电影类型或未开启剧集组刮削时，跳过剧集组处理，直接更新别名
                if show.get('type') == 'movie' or not enable_episode_group:
                    skip_reason = "电影类型" if show.get('type') == 'movie' else "未开启剧集组刮削"
                    self.logger.info(f"'{title}' {skip_reason}，跳过剧集组处理。")
                    # 更新别名到数据库
                    if aliases_to_update and any(aliases_to_update.values()):
                        updated_fields = await crud.update_anime_aliases_if_empty(session, anime_id, aliases_to_update, force_update=force_update)
                        if updated_fields:
                            mode_str = "(AI修正模式)" if force_update else ""
                            self.logger.info(f"为 '{title}' 更新了别名{mode_str}: {', '.join(updated_fields)}")
                    await session.commit()
                    processed_count += 1
                    continue

                tmdb_source = self.metadata_manager.sources.get("tmdb")
                if not tmdb_source or not hasattr(tmdb_source, 'get_all_episode_groups'):
                    self.logger.warning(f"TMDB源不支持 get_all_episode_groups 方法，跳过 '{title}' 的剧集组处理。")
                    # 即使不支持剧集组，也要更新别名
                    if aliases_to_update and any(aliases_to_update.values()):
                        updated_fields = await crud.update_anime_aliases_if_empty(session, anime_id, aliases_to_update, force_update=force_update)
                        if updated_fields:
                            mode_str = "(AI修正模式)" if force_update else ""
                            self.logger.info(f"为 '{title}' 更新了别名{mode_str}: {', '.join(updated_fields)}")
                    await session.commit()
                    processed_count += 1
                    continue

                season_matched = False
                groups_to_process = []
                all_groups = []

                # 第一级: 如果AI识别到季度信息,尝试使用seasons数据进行匹配
                if recognized_season is not None and details.seasons:
                    self.logger.info(f"第一级匹配: 使用seasons信息查找季度 {recognized_season}")

                    # 查找对应季度的season信息
                    target_season = None
                    for season in details.seasons:
                        if season.season_number == recognized_season:
                            target_season = season
                            break

                    if target_season:
                        self.logger.info(f"✓ 找到匹配的季度: {target_season.name} (ID: {target_season.id}, 集数: {target_season.episode_count})")
                        season_matched = True

                        # 获取所有剧集组,尝试找到匹配该季度的剧集组
                        all_groups = await tmdb_source.get_all_episode_groups(int(tmdb_id), user)
                        if all_groups:
                            self.logger.info(f"为 '{title}' 找到 {len(all_groups)} 个剧集组: {[g.get('name') for g in all_groups]}")

                            # 尝试找到匹配该季度的剧集组
                            for g in all_groups:
                                group_name = g.get('name', '').lower()

                                # 特殊处理第0季(特别季)
                                if recognized_season == 0:
                                    if ("special" in group_name or
                                        "season 0" in group_name or
                                        "s00" in group_name or
                                        "s0" in group_name or
                                        "特别" in group_name):
                                        groups_to_process.append(g)
                                        self.logger.info(f"✓ 找到特别季剧集组: {g.get('name')}")
                                else:
                                    # 匹配 "season 2", "第2季", "s02" 等格式
                                    if (f"season {recognized_season}" in group_name or
                                        f"第{recognized_season}季" in group_name or
                                        f"s{recognized_season:02d}" in group_name or
                                        f"s{recognized_season}" in group_name):
                                        groups_to_process.append(g)
                                        self.logger.info(f"✓ 找到匹配季度{recognized_season}的剧集组: {g.get('name')}")
                    else:
                        self.logger.warning(f"✗ 未找到季度 {recognized_season} 的seasons信息")

                # 第二级: 如果第一级没有找到,使用"Seasons"剧集组进行匹配
                if not season_matched or not groups_to_process:
                    self.logger.info(f"第二级匹配: 使用剧集组查找")

                    if not all_groups:
                        all_groups = await tmdb_source.get_all_episode_groups(int(tmdb_id), user)

                    if not all_groups:
                        self.logger.info(f"'{title}' (TMDB ID: {tmdb_id}) 没有找到任何剧集组。")
                        # 即使没有剧集组，也要更新别名
                        if aliases_to_update and any(aliases_to_update.values()):
                            updated_fields = await crud.update_anime_aliases_if_empty(session, anime_id, aliases_to_update, force_update=force_update)
                            if updated_fields:
                                mode_str = "(AI修正模式)" if force_update else ""
                                self.logger.info(f"为 '{title}' 更新了别名{mode_str}: {', '.join(updated_fields)}")
                        await session.commit()
                        processed_count += 1
                        continue

                    self.logger.info(f"为 '{title}' 找到 {len(all_groups)} 个剧集组: {[g.get('name') for g in all_groups]}")

                    # 优先选择"Seasons"剧集组
                    seasons_group = None
                    for g in all_groups:
                        group_name = g.get('name', '').lower()
                        if 'seasons' in group_name:
                            seasons_group = g
                            self.logger.info(f"✓ 找到'Seasons'剧集组: {g.get('name')}")
                            break

                    if seasons_group:
                        groups_to_process = [seasons_group]
                    else:
                        # 如果没有"Seasons"剧集组,使用默认逻辑(type=1的剧集组)
                        groups_to_process = [g for g in all_groups if g.get('type') == 1]
                        if groups_to_process:
                            self.logger.info(f"未找到'Seasons'剧集组,使用默认逻辑(type=1)")

                if not groups_to_process:
                    self.logger.info(f"'{title}' 没有找到“原始播出顺序”(type=1)的剧集组，跳过映射更新。")
                    # 即使没有剧集组，也要更新别名
                    if aliases_to_update and any(aliases_to_update.values()):
                        updated_fields = await crud.update_anime_aliases_if_empty(session, anime_id, aliases_to_update, force_update=force_update)
                        if updated_fields:
                            mode_str = "(AI修正模式)" if force_update else ""
                            self.logger.info(f"为 '{title}' 更新了别名{mode_str}: {', '.join(updated_fields)}")
                    await session.commit()
                    processed_count += 1
                    continue
                
                self.logger.info(f"为 '{title}' 选择了 {len(groups_to_process)} 个剧集组进行映射更新。")

                # 步骤 5: 为每个选定的剧集组，更新映射表
                for group in groups_to_process:
                    group_id = group.get('id')
                    if not group_id:
                        continue

                    self.logger.info(f"正在为 '{title}' 更新剧集组 '{group.get('name')}' (ID: {group_id}) 的映射...")
                    await self.metadata_manager.update_tmdb_mappings(int(tmdb_id), group_id, user)

                    # 步骤 6: 更新作品关联的主剧集组ID
                    await crud.update_anime_tmdb_group_id(session, anime_id, group_id)
                    self.logger.info(f"已将 '{title}' 的主剧集组ID更新为: {group_id}")
                    mapped_count += 1

                    # 步骤 7: 如果使用了剧集组匹配,在中文别名后追加季度信息
                    # 从剧集组名称中提取季度信息
                    group_name = group.get('name', '')
                    season_suffix = None

                    # 尝试从剧集组名称中提取季度
                    # 匹配 "Season 2", "第2季", "S02", "S2" 等格式
                    season_match = re.search(r'(?:season\s+|第|s)(\d+)(?:季)?', group_name, re.IGNORECASE)
                    if season_match:
                        season_num = int(season_match.group(1))
                        # 只为第二季及以后追加季度后缀
                        if season_num >= 2:
                            season_suffix = f" 第{season_num}季"
                            self.logger.info(f"检测到剧集组季度信息: {season_suffix}")

                            # 在AI验证后的别名上追加季度后缀
                            if aliases_to_update and aliases_to_update.get("aliases_cn"):
                                updated_cn_aliases = []
                                for cn_alias in aliases_to_update["aliases_cn"]:
                                    if cn_alias and not cn_alias.endswith(season_suffix):
                                        updated_cn_aliases.append(cn_alias + season_suffix)
                                    else:
                                        updated_cn_aliases.append(cn_alias)
                                aliases_to_update["aliases_cn"] = updated_cn_aliases
                                self.logger.info(f"已为 '{title}' 的AI验证后的中文别名追加季度后缀: {season_suffix}")
                        else:
                            self.logger.info(f"检测到第{season_num}季,跳过追加季度后缀(仅第二季及以后追加)")

                # 步骤 8: 更新别名到数据库（在剧集组处理完成后，可能已追加季度后缀）
                if aliases_to_update and any(aliases_to_update.values()):
                    updated_fields = await crud.update_anime_aliases_if_empty(session, anime_id, aliases_to_update, force_update=force_update)
                    if updated_fields:
                        mode_str = "(AI修正模式)" if force_update else ""
                        self.logger.info(f"为 '{title}' 更新了别名{mode_str}: {', '.join(updated_fields)}")

                await session.commit() # 提交本次节目的所有更改
                processed_count += 1

            except Exception as e:
                self.logger.error(f"处理 '{title}' 时发生错误: {e}", exc_info=True)
                await session.rollback() # 出错时回滚
            finally:
                await asyncio.sleep(1) # 简单的速率限制，防止对TMDB API造成过大压力

        self.logger.info(f"定时任务 [{self.job_name}] 执行完毕。")
        skip_info = f", 跳过 {skipped_count} 个已有数据" if skipped_count > 0 else ""
        self.logger.info(f"统计: 共处理 {processed_count}/{total_shows} 个作品, 刮削 {scraped_count} 个TMDB ID, 映射 {mapped_count} 个剧集组{skip_info}。")
        # 修正：抛出 TaskSuccess 异常，以便 TaskManager 可以用一个有意义的消息来结束任务
        raise TaskSuccess(f"任务执行完毕，共处理 {processed_count}/{total_shows} 个作品, 刮削 {scraped_count} 个TMDB ID, 映射 {mapped_count} 个剧集组{skip_info}。")