"""
刷新弹幕指令模块
提供 @SXDM 指令，刷新最近播放的弹幕

交互流程:
1. @SXDM → 显示最近播放的番剧列表（#A #B #C #D #E）
2. @SXDM #A → 显示该番剧的分集列表
3. @SXDM #A5 → 直接触发刷新任务
"""
import re
import logging
from typing import List, Dict, TYPE_CHECKING
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from .base import CommandHandler, _get_db_cache, _set_db_cache
from src.db import crud
from src.db.orm_models import Anime, AnimeSource, Episode
from src.services.task_manager import TaskManager
from src.services.scraper_manager import ScraperManager
from src.rate_limiter import RateLimiter
from src import tasks
from src.utils.image_utils import get_custom_domain

if TYPE_CHECKING:
    from src.api.dandan import DandanSearchAnimeResponse, DandanSearchAnimeItem

logger = logging.getLogger(__name__)


class RefreshDanmakuCommand(CommandHandler):
    """
    刷新弹幕指令: @SXDM

    支持三种使用方式:
    1. @SXDM - 显示最近播放的番剧列表
    2. @SXDM #A - 显示指定番剧的分集列表
    3. @SXDM #A5 - 直接刷新指定番剧的第5集
    """

    # 番剧标签映射
    ANIME_LABELS = ['#A', '#B', '#C', '#D', '#E']

    # 会话缓存时间（秒），用于保存用户的选择状态
    SESSION_TTL = 1800  # 30分钟

    def __init__(self):
        super().__init__(
            name="SXDM",
            description="刷新最近播放的弹幕",
            cooldown_seconds=2,
            usage="@SXDM [标签] [集数] (支持大小写)",
            examples=[
                "@SXDM - 查看最近播放",
                "@sxdm #a - 查看A番剧的分集",
                "@SXDM #A5 - 刷新A番剧第5集",
                "@sxdm #a5 - 小写也可以"
            ]
        )

    async def execute(self, token: str, args: List[str], session: AsyncSession,
                     config_manager, **kwargs) -> "DandanSearchAnimeResponse":
        """执行刷新指令"""

        # 获取图片URL
        image_url = await self.get_image_url(config_manager)
        custom_domain = await get_custom_domain(config_manager)

        # 获取会话状态（用于缓存番剧和分集信息）
        session_key = f"cmd_session_{token}"
        session_state = await _get_db_cache(session, "", session_key)
        if not session_state:
            session_state = {}

        # 阶段1: 没有参数 → 显示番剧列表
        if not args:
            return await self._show_anime_list(token, session, session_key, custom_domain, image_url)

        # 解析参数
        arg = args[0].upper()

        # 检查参数格式
        # 匹配 #A5 格式（标签+数字）
        match_episode = re.match(r'^(#[A-E])(\d+)$', arg)
        # 匹配 #A 格式（只有标签）
        match_label = re.match(r'^#[A-E]$', arg)

        if match_episode:
            # 格式: #A5 → 直接触发刷新
            label = match_episode.group(1)
            episode_number = match_episode.group(2)

            # 获取依赖
            task_manager: TaskManager = kwargs.get('task_manager')
            scraper_manager: ScraperManager = kwargs.get('scraper_manager')
            rate_limiter: RateLimiter = kwargs.get('rate_limiter')

            if not all([task_manager, scraper_manager, rate_limiter]):
                return self.error_response(
                    "系统依赖缺失",
                    "无法获取必要的系统组件",
                    image_url
                )

            return await self._trigger_refresh_by_label(
                token, session, session_key, session_state,
                label, episode_number,
                task_manager, scraper_manager, rate_limiter, config_manager,
                custom_domain, image_url
            )

        elif match_label:
            # 格式: #A → 显示分集列表
            return await self._show_episode_list(
                token, session, session_key, session_state, arg, custom_domain, image_url
            )

        else:
            # 无效格式
            return self.error_response(
                "无效的参数格式",
                f"参数 '{arg}' 格式不正确\n\n💡 正确格式:\n• @SXDM #A - 查看分集列表\n• @SXDM #A5 - 刷新第5集",
                image_url
            )



    async def _show_anime_list(
        self,
        token: str,
        session: AsyncSession,
        session_key: str,
        custom_domain: str,
        image_url: str
    ) -> "DandanSearchAnimeResponse":
        """显示最近播放的番剧列表"""

        # 读取播放历史
        cache_key = f"play_history_{token}"
        history = await _get_db_cache(session, "", cache_key)

        logger.info(f"@SXDM 查询播放历史: token={token[:8]}..., cache_key={cache_key}, result={history}")

        if not history:
            history = []

        if not history:
            time_desc = f"{self.SESSION_TTL // 60}分钟有效"
            item = self.build_response_item(
                anime_id=999999997,
                title="未找到最近播放记录",
                description=f"💡 提示: 播放视频后会自动记录 ({time_desc})",
                image_url=image_url
            )
            return self.build_response([item])

        # 查询每部番剧的总集数
        anime_list = []
        for idx, record in enumerate(history[:5]):  # 只显示最近5部
            anime_id = record["animeId"]

            # 查询总集数（通过 AnimeSource 关联）
            stmt = (
                select(func.count(Episode.id))
                .join(AnimeSource, Episode.sourceId == AnimeSource.id)
                .where(AnimeSource.animeId == anime_id)
            )
            result = await session.execute(stmt)
            total_episodes = result.scalar() or 0

            logger.info(
                f"@SXDM 查询番剧集数: animeId={anime_id}, "
                f"title={record['animeTitle']}, total_episodes={total_episodes}"
            )

            anime_list.append({
                "label": self.ANIME_LABELS[idx],
                "animeId": anime_id,
                "animeTitle": record["animeTitle"],
                "totalEpisodes": total_episodes
            })

        logger.info(f"@SXDM 构建番剧列表完成: anime_list={anime_list}")

        # 保存会话状态
        session_state = {
            "command": "SXDM",
            "stage": "select_anime",
            "data": {"animeList": anime_list},
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        await _set_db_cache(session, "", session_key, session_state, self.SESSION_TTL)

        # 记录执行时间
        await self.record_execution(token, session)

        # 构建标签列表提示
        labels = [anime["label"] for anime in anime_list]
        labels_text = " ".join(labels)

        # 第一条：引导说明
        # 动态计算时间显示（转换为分钟）
        time_desc = f"{self.SESSION_TTL // 60}分钟内" if self.SESSION_TTL >= 60 else f"{self.SESSION_TTL}秒内"

        anime_items = [
            self.build_response_item(
                anime_id=999999998,
                title=f"📺 最近播放的番剧 ({time_desc})",
                description=f"请选择要刷新的剧集作品:\n\n可用标签: {labels_text}\n\n"
                           f"💡 使用方法:\n• @SXDM #A - 查看分集列表\n• @SXDM #A5 - 直接刷新第5集",
                image_url=image_url,
                episodeCount=len(anime_list)
            )
        ]

        # 第二条开始：每部番剧
        for anime in anime_list:
            # 优先使用番剧自己的海报，如果没有则使用默认图片
            anime_image = anime.get("imageUrl") or anime.get("localImagePath") or image_url
            # 如果是本地路径且设置了自定义域名，则添加域名前缀
            if anime_image and not anime_image.startswith(("http://", "https://", "/")):
                anime_image = f"{custom_domain}/{anime_image}" if custom_domain else f"/{anime_image}"

            anime_items.append(
                self.build_response_item(
                    anime_id=anime["animeId"],
                    title=f"{anime['label']} {anime['animeTitle']}",
                    description=f"最近播放 | 共 {anime['totalEpisodes']} 集",
                    image_url=anime_image,
                    type="tvseries",
                    episodeCount=anime["totalEpisodes"]
                )
            )

        logger.info(f"@SXDM 返回响应: 返回 {len(anime_items)} 条记录 (1条引导 + {len(anime_list)}部番剧)")

        return self.build_response(anime_items)

    async def _show_episode_list(
        self,
        token: str,
        session: AsyncSession,
        session_key: str,
        session_state: Dict,
        selected_label: str,
        custom_domain: str,
        image_url: str
    ) -> "DandanSearchAnimeResponse":
        """显示选中番剧的分集列表"""

        anime_list = session_state.get("data", {}).get("animeList", [])

        # 查找选中的番剧
        selected_anime = None
        for anime in anime_list:
            if anime["label"] == selected_label:
                selected_anime = anime
                break

        if not selected_anime:
            return self.error_response(
                "无效的标签",
                f"标签 '{selected_label}' 不存在\n💡 请输入 @SXDM 查看可用标签",
                image_url
            )

        anime_id = selected_anime["animeId"]

        # 查询番剧的海报信息
        anime_stmt = select(Anime.imageUrl, Anime.localImagePath).where(Anime.id == anime_id)
        anime_result = await session.execute(anime_stmt)
        anime_row = anime_result.first()
        anime_image_url = None
        if anime_row:
            anime_image_url = anime_row[0] or anime_row[1]  # imageUrl 或 localImagePath
            # 处理本地路径
            if anime_image_url and not anime_image_url.startswith(("http://", "https://", "/")):
                anime_image_url = f"{custom_domain}/{anime_image_url}" if custom_domain else f"/{anime_image_url}"

        # 如果没有找到番剧海报，使用默认图片
        if not anime_image_url:
            anime_image_url = image_url

        # 查询分集列表（通过 AnimeSource 关联，按集数排序）
        stmt = (
            select(Episode)
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(AnimeSource.animeId == anime_id)
            .order_by(Episode.episodeIndex)
        )
        result = await session.execute(stmt)
        episodes = result.scalars().all()

        if not episodes:
            return self.error_response(
                "未找到分集信息",
                f"番剧 '{selected_anime['animeTitle']}' 没有分集数据",
                image_url
            )

        # 构建分集信息（使用 Episode.commentCount 字段）
        episode_list = []
        for ep in episodes:
            count = ep.commentCount or 0
            status = "已缓存" if count > 0 else "未缓存"
            episode_list.append({
                "index": len(episode_list) + 1,
                "episodeId": ep.id,
                "episodeTitle": ep.title or f"第{ep.episodeIndex}话",
                "commentCount": count,
                "status": status
            })

        # 更新会话状态
        session_state["stage"] = "select_episode"
        session_state["data"]["selectedAnime"] = selected_anime
        session_state["data"]["episodes"] = episode_list
        await _set_db_cache(session, "", session_key, session_state, self.SESSION_TTL)

        # 第一条：引导说明
        anime_items = [
            self.build_response_item(
                anime_id=999999996,
                title=f"📺 {selected_anime['animeTitle']} - 分集列表",
                description=f"请选择要刷新的集数:\n\n共 {len(episode_list)} 集\n\n"
                           f"💡 输入 @SXDM 标签+集数 刷新弹幕\n例如: @SXDM {selected_anime['label']}5 (刷新第5集)",
                image_url=anime_image_url,  # 使用番剧的海报
                episodeCount=len(episode_list)
            )
        ]

        # 第二条开始：每个分集（限制显示前50集）
        # 提取标签字母（#A -> A）
        label_prefix = selected_anime['label'][1:]  # 去掉 # 号

        for ep in episode_list[:50]:
            # 使用虚拟ID（900000000 + 索引），避免ID过大导致客户端解析错误
            virtual_id = 900000000 + ep['index']

            anime_items.append(
                self.build_response_item(
                    anime_id=virtual_id,
                    title=f"[{label_prefix}{ep['index']}] {ep['episodeTitle']}",
                    description=f"{ep['status']} | 弹幕数: {ep['commentCount']} 条",
                    image_url=anime_image_url,  # 使用番剧的海报
                    type="tvseries",
                    episodeCount=1
                )
            )

        logger.info(
            f"@SXDM 返回分集列表: animeId={anime_id}, "
            f"total={len(episode_list)}, displayed={min(50, len(episode_list))}"
        )

        return self.build_response(anime_items)

    async def _trigger_refresh_by_label(
        self,
        token: str,
        session: AsyncSession,
        session_key: str,
        session_state: Dict,
        label: str,
        episode_number: str,
        task_manager,
        scraper_manager,
        rate_limiter,
        config_manager,
        custom_domain: str,
        image_url: str
    ) -> "DandanSearchAnimeResponse":
        """根据标签和集数触发刷新任务（格式: #A5）"""

        # 解析集数编号
        try:
            ep_num = int(episode_number)
        except ValueError:
            return self.error_response(
                "无效的集数",
                f"集数 '{episode_number}' 不是有效的数字",
                image_url
            )

        # 从播放历史中获取番剧列表
        cache_key = f"play_history_{token}"
        history = await _get_db_cache(session, "", cache_key)
        if not history:
            return self.error_response(
                "未找到播放历史",
                "请先播放视频后再使用刷新功能",
                image_url
            )

        # 构建标签到番剧的映射
        anime_list = []
        for idx, record in enumerate(history[:5]):
            anime_list.append({
                "label": self.ANIME_LABELS[idx],
                "animeId": record["animeId"],
                "animeTitle": record["animeTitle"]
            })

        # 查找对应标签的番剧
        selected_anime = None
        for anime in anime_list:
            if anime["label"] == label:
                selected_anime = anime
                break

        if not selected_anime:
            labels = " ".join([a["label"] for a in anime_list])
            return self.error_response(
                "无效的标签",
                f"标签 '{label}' 不存在\n💡 可用标签: {labels}",
                image_url
            )

        anime_id = selected_anime["animeId"]
        anime_title = selected_anime["animeTitle"]

        # 查询该番剧的所有分集（按集数排序）
        stmt = (
            select(Episode)
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(AnimeSource.animeId == anime_id)
            .order_by(Episode.episodeIndex)
        )
        result = await session.execute(stmt)
        episodes = result.scalars().all()

        if not episodes:
            return self.error_response(
                "未找到番剧分集信息",
                f"番剧: {anime_title}",
                image_url
            )

        # 验证集数编号
        if ep_num < 1 or ep_num > len(episodes):
            return self.error_response(
                "无效的集数",
                f"集数: {ep_num}\n番剧: {anime_title}\n可用集数: 1-{len(episodes)}",
                image_url
            )

        # 获取对应分集
        selected_episode = episodes[ep_num - 1]
        episode_id = selected_episode.id
        episode_title = selected_episode.title or f"第{selected_episode.episodeIndex}话"

        # 验证分集存在
        info = await crud.get_episode_for_refresh(session, episode_id)
        if not info:
            return self.error_response(
                "分集信息异常",
                f"分集: {episode_title}",
                image_url
            )

        # 提交刷新任务
        try:
            unique_key = f"refresh-episode-{episode_id}"

            task_id, _ = await task_manager.submit_task(
                lambda s, cb: tasks.refresh_episode_task(
                    episode_id, s, scraper_manager, rate_limiter, cb, config_manager
                ),
                f"指令刷新: {anime_title} - {episode_title}",
                unique_key=unique_key
            )

            # 记录执行时间
            await self.record_execution(token, session)

            logger.info(
                f"@SXDM 提交刷新任务: label={label}, episode_number={ep_num}, "
                f"episodeId={episode_id}, anime={anime_title}, taskId={task_id}"
            )

            message = (
                f"✓ 刷新任务已提交\n\n"
                f"番剧: {anime_title}\n"
                f"分集: [{ep_num}] {episode_title}\n"
                f"任务ID: {task_id}\n\n"
                f"🔄 任务处理中，请稍候15秒后重新获取弹幕"
            )

            item = self.build_response_item(
                anime_id=999999995,
                title="✓ 弹幕刷新任务已提交",
                description=message,
                image_url=image_url
            )

            return self.build_response([item])

        except Exception as e:
            logger.error(f"@SXDM 提交刷新任务失败: {e}", exc_info=True)
            return self.error_response(
                "任务提交失败",
                f"错误详情: {str(e)}",
                image_url
            )


