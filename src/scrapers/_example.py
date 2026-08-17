"""
=================================================================
示例弹幕源 (Example Scraper) — 开发新弹幕源的参考模板
=================================================================

🔧 如何使用此模板：
  1. 复制此文件并重命名为你的源名称，例如 `mysite.py`
     注意：文件名不要以 `_` 开头，否则 scraper_manager 会跳过它
  2. 修改 provider_name 和各个方法的实现
  3. 重启服务，scraper_manager 会自动发现并加载你的源

📝 注意事项：
  - 此文件以 `_` 开头，不会被自动加载，仅作参考
  - scraper_manager 的自动发现机制：
    遍历 src/scrapers/ 下所有 .py 文件，跳过 `_` 开头和 `base` 命名的文件，
    找到 BaseScraper 子类后自动实例化
  - 所有抽象方法都必须实现，否则实例化时会报错
  - HTTP 请求推荐使用 self._create_client() 创建 httpx.AsyncClient
  - 缓存操作使用 self._get_from_cache() 和 self._set_to_cache()

=================================================================
"""

# 版本号：scraper_manager 会读取此变量并存入数据库
__version__ = "1.0.0"

import logging
import re
import time
from typing import Optional, List

from src.db.models import ProviderSearchInfo, ProviderEpisodeInfo
from src.scrapers.base import BaseScraper, get_season_from_title, track_performance
from src.utils import parse_search_keyword

# 模块级 logger（一般不直接使用，基类已提供 self.logger）
logger = logging.getLogger(__name__)


class ExampleScraper(BaseScraper):
    """
    示例弹幕源实现。

    继承 BaseScraper 后需要实现以下抽象方法：
    - search()           : 搜索动画
    - get_episodes()     : 获取某部动画的分集列表
    - get_comments()     : 获取某集的弹幕
    - get_info_from_url(): 从URL解析动画信息 (用于自定义URL导入)
    - get_id_from_url()  : 从URL提取媒体ID
    - close()            : 释放资源（关闭HTTP客户端等）
    """

    # ─────────────────── 1. 类属性（必须） ───────────────────

    # 源的唯一标识符，全局唯一，用于数据库存储和匹配
    provider_name = "example"

    # ─────────────────── 2. 类属性（可选，按需覆盖） ───────────────────

    # 此源处理的域名列表，用于 get_info_from_url / get_id_from_url 的 URL 匹配
    handled_domains = ["www.example.com", "example.com"]

    # Referer 头，部分网站需要正确的 Referer 才能访问 API
    referer = "https://www.example.com"

    # 是否记录 HTTP 响应日志（调试用，生产环境建议 False）
    is_loggable = False

    # 速率限制：每分钟最大请求数（None 表示不限制）
    rate_limit_quota: Optional[int] = 60

    # 弹幕"火热"阈值：弹幕数超过此值时标记为热门
    likes_fire_threshold = 1000

    # 源专属的分集黑名单正则（过滤预告、花絮等垃圾分集）
    # 会与 base.py 中的 COMMON_EPISODE_BLACKLIST_REGEX 合并使用
    _PROVIDER_SPECIFIC_BLACKLIST_DEFAULT = r"预告|花絮|CM|PV"

    # 可配置字段：会在前端设置页面中显示，允许用户在 UI 中修改
    # key=配置键名, value={"label": 显示名称, "type": 字段类型, "default": 默认值}
    # type 可选: "text", "password", "number", "boolean", "select"
    configurable_fields = {
        "exampleApiKey": {
            "label": "API密钥",
            "type": "password",
            "default": "",
        },
        "exampleQuality": {
            "label": "画质偏好",
            "type": "select",
            "default": "1080p",
            "options": ["720p", "1080p", "4K"],
        },
    }

    # 默认配置值：scraper_manager 首次加载时会写入数据库
    _DEFAULT_CONFIGS = {
        "exampleApiKey": "",
        "exampleQuality": "1080p",
    }

    # ─────────────────── 3. 初始化 ───────────────────

    def __init__(self, session_factory, config_manager, transport_manager):
        """
        构造函数。scraper_manager 会传入三个参数：
        - session_factory  : SQLAlchemy async_sessionmaker，用于数据库操作
        - config_manager   : ConfigManager 实例，用于读取/写入配置
        - transport_manager: TransportManager 实例，用于代理支持
        """
        super().__init__(session_factory, config_manager, transport_manager)
        self._client = None  # httpx.AsyncClient 实例（懒初始化）

    # ─────────────────── 4. 必须实现的抽象方法 ───────────────────

    @track_performance  # 装饰器：自动记录方法耗时到日志
    async def search(self, keyword: str, episode_info: Optional[dict] = None) -> List[ProviderSearchInfo]:
        """
        搜索动画。

        Args:
            keyword: 用户输入的搜索关键词
            episode_info: 可选字典，包含 'season' 和 'episode' 信息（后备搜索时传入）

        Returns:
            ProviderSearchInfo 列表，每个元素代表一个搜索结果
        """
        # parse_search_keyword 可以从关键词中解析出标题、季度、集数等信息
        parsed = parse_search_keyword(keyword)
        search_title = parsed["title"]
        season_filter = parsed.get("season")

        results = []
        try:
            # ── 日志：方法入口 ──
            self.logger.info(f"开始搜索: {keyword}")

            # 使用 _create_client() 创建带代理支持的 httpx.AsyncClient
            async with self._create_client() as client:
                resp = await client.get(
                    "https://api.example.com/search",
                    params={"q": search_title},
                )
                resp.raise_for_status()
                data = resp.json()

            # ── 日志：记录原始 HTTP 响应（调试用） ──
            # _should_log_responses() 由数据库配置控制，生产环境一般关闭
            if await self._should_log_responses():
                await self._log_raw_response(resp.text, "Search Response")

            for item in data.get("results", []):
                # get_season_from_title() 可以从标题中提取季度信息
                detected_season = get_season_from_title(item["title"])
                if season_filter and detected_season and detected_season != season_filter:
                    # ── 日志：过滤细节用 debug 级别 ──
                    self.logger.debug(f"过滤不匹配季度: {item['title']} (S{detected_season})")
                    continue

                results.append(ProviderSearchInfo(
                    provider=self.provider_name,        # 源标识符
                    mediaId=str(item["id"]),            # 媒体ID（字符串）
                    title=item["title"],                # 标题
                    type="tvseries",                    # 类型: tvseries / movie / ova / web
                    season=detected_season,             # 季度（可选, int）
                    year=item.get("year"),              # 年份（可选, int）
                    imageUrl=item.get("cover"),         # 封面图URL（可选）
                    episodeCount=item.get("ep_count"),  # 总集数（可选, int）
                ))

            # ── 日志：搜索结果汇总 ──
            if results:
                self.logger.info(f"搜索完成，找到 {len(results)} 个结果")
            else:
                self.logger.info(f"搜索 '{keyword}' 未找到结果")

        # ── 日志：按异常类型分级 ──
        except (Exception,) as e:
            # 实际开发中建议拆分为更细的异常类型：
            # except (httpx.TimeoutException, httpx.ConnectError) as e:
            #     self.logger.warning(f"搜索超时或网络错误: {e}")
            # except httpx.HTTPError as e:
            #     self.logger.error(f"搜索HTTP请求失败: {e}")
            # except Exception as e:
            #     self.logger.warning(f"搜索发生未知错误: {type(e).__name__}")
            self.logger.error(f"搜索失败: {e}")

        return results

    @track_performance
    async def get_episodes(self, media_id: str, target_episode_index: Optional[int] = None,
                           db_media_type: Optional[str] = None) -> List[ProviderEpisodeInfo]:
        """
        获取指定动画的分集列表。

        Args:
            media_id: search() 返回的 mediaId
            target_episode_index: 目标集数（可选），传入时可优化为只获取到该集为止
            db_media_type: 数据库中的媒体类型（'movie'/'tv_series'），可指导刮削策略

        Returns:
            ProviderEpisodeInfo 列表，每个元素代表一集
        """
        _start_time = time.time()

        # 先检查缓存（base 类提供的缓存机制）
        cache_key = f"episodes_{media_id}"
        cached = await self._get_from_cache(cache_key)
        if cached is not None:
            self.logger.info(f"从缓存中命中分集列表 (media_id={media_id})")
            return cached

        episodes = []
        filtered_out = []
        try:
            self.logger.info(f"开始获取分集列表: media_id={media_id}")

            async with self._create_client() as client:
                resp = await client.get(f"https://api.example.com/anime/{media_id}/episodes")
                resp.raise_for_status()
                data = resp.json()

            # ── 日志：记录原始 HTTP 响应 ──
            if await self._should_log_responses():
                await self._log_raw_response(resp.text, "Episodes Response", media_id=media_id)

            for ep in data.get("episodes", []):
                episodes.append(ProviderEpisodeInfo(
                    provider=self.provider_name,
                    episodeId=str(ep["id"]),                     # 分集ID（字符串）
                    title=ep.get("title", f"第{ep['index']}集"),  # 分集标题
                    episodeIndex=ep["index"],                     # 分集序号（整数, 从1开始）
                ))

            # 使用基类的黑名单过滤垃圾分集（预告、花絮等）
            # 返回 (保留的分集, 被过滤的分集信息)
            episodes, filtered_out = await self._filter_junk_episodes(episodes, return_filtered=True)

            # 写入缓存（需要传入配置键和默认TTL秒数）
            if episodes:
                await self._set_to_cache(cache_key, episodes,
                                         config_key="example_episodes_cache_ttl",
                                         default_ttl=3600)  # 默认缓存1小时

        except Exception as e:
            self.logger.error(f"获取分集列表失败: {e}")

        # 统一日志：同时显示保留的分集和被过滤的分集
        _elapsed_ms = int((time.time() - _start_time) * 1000)
        self._log_episodes_result(episodes, filtered_out, _elapsed_ms, target_episode_index)

        if target_episode_index is not None:
            return [ep for ep in episodes if ep.episodeIndex == target_episode_index]
        return episodes

    @track_performance
    async def get_comments(self, episode_id: str, progress_callback=None) -> List[dict]:
        """
        获取指定分集的弹幕列表。

        Args:
            episode_id: get_episodes() 返回的 episodeId
            progress_callback: 可选的进度回调函数，用于报告下载进度

        Returns:
            弹幕列表，每个元素是一个 dict，包含以下字段：
            - time   : float, 弹幕出现的时间（秒）
            - mode   : int, 弹幕模式（1=滚动, 4=底部, 5=顶部）
            - color  : int, 颜色值（十进制 RGB, 例如 16777215=白色）
            - content: str, 弹幕文本内容
        """
        comments = []
        try:
            self.logger.info(f"开始获取弹幕: episode_id={episode_id}")

            async with self._create_client() as client:
                resp = await client.get(f"https://api.example.com/comments/{episode_id}")
                resp.raise_for_status()
                data = resp.json()

            # ── 日志：记录原始 HTTP 响应 ──
            if await self._should_log_responses():
                await self._log_raw_response(resp.text, "Comments Response", episode_id=episode_id)

            for c in data.get("comments", []):
                comments.append({
                    "time": c["time"],
                    "mode": c.get("mode", 1),
                    "color": c.get("color", 16777215),  # 默认白色
                    "content": c["text"],
                })

            # ── 日志：弹幕结果汇总 ──
            if comments:
                self.logger.info(f"成功获取 {len(comments)} 条弹幕 (episode_id={episode_id})")
            else:
                self.logger.info(f"该视频暂无弹幕数据 (episode_id={episode_id})")

        except Exception as e:
            self.logger.error(f"获取弹幕失败: {e}")

        return comments

    async def get_info_from_url(self, url: str) -> Optional[ProviderSearchInfo]:
        """
        从URL解析动画信息（用于"自定义URL导入"功能）。

        当用户在前端粘贴一个 URL 时，系统会遍历所有源的 handled_domains，
        找到匹配的源后调用此方法解析出动画信息。

        Args:
            url: 用户输入的URL，例如 "https://www.example.com/anime/12345"

        Returns:
            ProviderSearchInfo 或 None（解析失败时）
        """
        media_id = await self.get_id_from_url(url)
        if not media_id:
            self.logger.warning(f"无法从URL中提取信息: {url}")
            return None

        try:
            async with self._create_client() as client:
                resp = await client.get(f"https://api.example.com/anime/{media_id}")
                resp.raise_for_status()
                data = resp.json()

            self.logger.info(f"成功从URL解析: {data['title']} (media_id={media_id})")
            return ProviderSearchInfo(
                provider=self.provider_name,
                mediaId=media_id,
                title=data["title"],
                type=data.get("type", "tvseries"),
                url=url,
            )
        except Exception as e:
            self.logger.error(f"URL解析失败: {e}")
            return None

    async def get_id_from_url(self, url: str) -> Optional[str]:
        """
        从URL中提取媒体ID。

        Args:
            url: 例如 "https://www.example.com/anime/12345"

        Returns:
            媒体ID字符串，例如 "12345"；解析失败返回 None
        """
        match = re.search(r'/anime/(\d+)', url)
        if not match:
            self.logger.warning(f"无法从URL中提取ID: {url}")
            return None
        return match.group(1)

    async def close(self):
        """
        释放资源。服务关闭时会调用此方法。
        如果你持有长连接的 HTTP 客户端，在此处关闭。
        """
        if self._client:
            await self._client.aclose()
            self._client = None

    # ─────────────────── 5. 可选覆盖的方法 ───────────────────

    def build_media_url(self, media_id: str) -> str:
        """
        构建媒体页面URL（可选覆盖）。
        用于在前端显示"查看原始页面"链接。默认返回空字符串。
        """
        return f"https://www.example.com/anime/{media_id}"

    def format_episode_id_for_comments(self, episode_id: str) -> str:
        """
        格式化分集ID用于弹幕请求（可选覆盖）。
        某些源的弹幕API需要特殊格式的ID，可在此转换。默认直接返回原始 episode_id。
        """
        return episode_id