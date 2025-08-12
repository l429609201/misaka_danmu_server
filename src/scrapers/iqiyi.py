import asyncio
import logging
import aiomysql
import re
import json
from typing import ClassVar
import zlib
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Callable
from collections import defaultdict
import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from .. import models
from .base import BaseScraper, get_season_from_title

# --- Pydantic Models for iQiyi API (部分模型现在仅用于旧缓存兼容或作为新API响应的子集) ---

class IqiyiVideoLibMeta(BaseModel):
    douban_id: Optional[int] = Field(None, alias="douban_id")

class IqiyiSearchVideoInfo(BaseModel):
    item_link: str = Field(alias="itemLink")

class IqiyiSearchAlbumInfo(BaseModel):
    album_id: Optional[int] = Field(None, alias="albumId")
    item_total_number: Optional[int] = Field(None, alias="itemTotalNumber")
    site_id: Optional[str] = Field(None, alias="siteId")
    album_link: Optional[str] = Field(None, alias="albumLink")
    video_doc_type: int = Field(alias="videoDocType")
    album_title: Optional[str] = Field(None, alias="albumTitle")
    channel: Optional[str] = None
    release_date: Optional[str] = Field(None, alias="releaseDate")
    album_img: Optional[str] = Field(None, alias="albumImg")
    video_lib_meta: Optional[IqiyiVideoLibMeta] = Field(None, alias="video_lib_meta")
    videoinfos: Optional[List[IqiyiSearchVideoInfo]] = None

    @property
    def link_id(self) -> Optional[str]:
        link_to_parse = self.album_link
        if self.videoinfos and self.videoinfos[0].item_link and self.album_link:
            link_to_parse = self.videoinfos[0].item_link

        match = re.search(r"v_(\w+?)\.html", link_to_parse)
        return match.group(1).strip() if match else None

    @property
    def year(self) -> Optional[int]:
        if self.release_date and len(self.release_date) >= 4:
            try:
                return int(self.release_date[:4])
            except ValueError:
                return None
        return None

class IqiyiAlbumDoc(BaseModel):
    score: float
    album_doc_info: IqiyiSearchAlbumInfo = Field(alias="albumDocInfo")

class IqiyiSearchDoc(BaseModel):
    docinfos: List[IqiyiAlbumDoc]

class IqiyiSearchResult(BaseModel):
    data: IqiyiSearchDoc

class IqiyiHtmlAlbumInfo(BaseModel):
    video_count: Optional[int] = Field(None, alias="videoCount")

# 修正：此模型现在用于解析新的 baseinfo API 响应
class IqiyiHtmlVideoInfo(BaseModel):
    album_id: int = Field(alias="albumId")
    tv_id: Optional[int] = Field(None, alias="tvId")
    video_id: Optional[int] = Field(None, alias="videoId")
    # 修正：新API返回的字段名不同
    video_name: str = Field(alias="name")
    video_url: str = Field(alias="playUrl")
    channel_name: Optional[str] = Field(None, alias="channelName")
    duration: int = Field(alias="durationSec")
    # video_count 不再从此模型获取，但保留字段以兼容旧缓存
    video_count: int = 0 

    @model_validator(mode='after')
    def merge_ids(self) -> 'IqiyiHtmlVideoInfo':
        if self.tv_id is None and self.video_id is not None:
            self.tv_id = self.video_id
        return self

class IqiyiEpisodeInfo(BaseModel):
    tv_id: int = Field(alias="tvId")
    name: str
    order: int
    play_url: str = Field(alias="playUrl")

    @property
    def link_id(self) -> Optional[str]:
        match = re.search(r"v_(\w+?)\.html", self.play_url)
        return match.group(1).strip() if match else None

class IqiyiVideoData(BaseModel):
    epsodelist: List[IqiyiEpisodeInfo]

class IqiyiVideoResult(BaseModel):
    data: IqiyiVideoData

class IqiyiUserInfo(BaseModel):
    uid: str

class IqiyiComment(BaseModel):
    content_id: str = Field(alias="contentId")
    content: str
    show_time: int = Field(alias="showTime")
    color: str
    # user_info 字段在XML中可能不存在，设为可选
    user_info: Optional[IqiyiUserInfo] = Field(None, alias="userInfo")

# --- Main Scraper Class ---

class IqiyiScraper(BaseScraper):
    provider_name = "iqiyi"
    _EPISODE_BLACKLIST_PATTERN = re.compile(r"加更|走心|解忧|纯享", re.IGNORECASE)
    # 新增：用于过滤搜索结果中非正片内容的正则表达式
    _SEARCH_JUNK_TITLE_PATTERN = re.compile(
        r'纪录片|预告|花絮|专访|MV|特辑|演唱会|音乐会|独家|解读|揭秘|赏析|速看|资讯|彩蛋|访谈|番外|短片',
        re.IGNORECASE
    )


    def __init__(self, pool: aiomysql.Pool):
        super().__init__(pool)
        self.mobile_user_agent = "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36 Edg/136.0.0.0"
        self.reg_video_info = re.compile(r'"videoInfo":(\{.+?\}),')

        self.client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)

    async def close(self):
        await self.client.aclose()

    async def search(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        # 修正：缓存键必须包含分集信息，以区分对同一标题的不同分集搜索
        cache_key_suffix = f"_s{episode_info['season']}e{episode_info['episode']}" if episode_info else ""
        cache_key = f"search_{self.provider_name}_{keyword}{cache_key_suffix}"
        cached_results = await self._get_from_cache(cache_key)
        if cached_results is not None:
            self.logger.info(f"爱奇艺: 从缓存中命中搜索结果 '{keyword}{cache_key_suffix}'")
            return [models.ProviderSearchInfo.model_validate(r) for r in cached_results]

        self.logger.info(f"爱奇艺: 正在搜索 '{keyword}'...")

        url = f"https://search.video.iqiyi.com/o?if=html5&key={keyword}&pageNum=1&pageSize=20"
        results = []
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            data = IqiyiSearchResult.model_validate(response.json())

            if not data.data or not data.data.docinfos:
                return []

            for doc in data.data.docinfos:
                if doc.score < 0.7: continue
                
                album = doc.album_doc_info
                if not (album.album_link and "iqiyi.com" in album.album_link and album.site_id == "iqiyi" and album.video_doc_type == 1):
                    continue
               # 修正：增加对 album.channel 的非空检查，并添加对“纪录片”频道的过滤
                if album.channel and ("原创" in album.channel or "教育" in album.channel or "纪录片" in album.channel):
                    self.logger.debug(f"爱奇艺: 根据频道 '{album.channel}' 过滤掉 '{album.album_title}'")
                    continue

                # 新增：根据标题过滤掉非正片内容
                if album.album_title and self._SEARCH_JUNK_TITLE_PATTERN.search(album.album_title):
                    self.logger.debug(f"爱奇艺: 根据标题黑名单过滤掉 '{album.album_title}'")
                    continue

                douban_id = None
                if album.video_lib_meta and album.video_lib_meta.douban_id:
                    douban_id = str(album.video_lib_meta.douban_id)

                link_id = album.link_id
                if not link_id:
                    continue

                channel_name = album.channel.split(',')[0] if album.channel else ""
                media_type = "movie" if channel_name == "电影" else "tv_series"

                current_episode = episode_info.get("episode") if episode_info else None
                cleaned_title = re.sub(r'<[^>]+>', '', album.album_title).replace(":", "：") if album.album_title else "未知标题"
                provider_search_info = models.ProviderSearchInfo(
                    provider=self.provider_name,
                    mediaId=link_id,
                    title=cleaned_title,
                    type=media_type,
                    season=get_season_from_title(cleaned_title),
                    year=album.year,
                    imageUrl=album.album_img,
                    douban_id=douban_id,
                    episodeCount=album.item_total_number,
                    currentEpisodeIndex=current_episode,
                )
                self.logger.debug(f"爱奇艺: 创建的 ProviderSearchInfo: {provider_search_info.model_dump_json(indent=2)}")
                results.append(provider_search_info)

        except Exception as e:
            self.logger.error(f"爱奇艺: 搜索 '{keyword}' 失败: {e}", exc_info=True)

        self.logger.info(f"爱奇艺: 搜索 '{keyword}' 完成，找到 {len(results)} 个有效结果。")
        if results:
            log_results = "\n".join([f"  - {r.title} (ID: {r.mediaId}, 类型: {r.type}, 年份: {r.year or 'N/A'})" for r in results])
            self.logger.info(f"爱奇艺: 搜索结果列表:\n{log_results}")
        results_to_cache = [r.model_dump() for r in results]
        await self._set_to_cache(cache_key, results_to_cache, 'search_ttl_seconds', 300)
        return results

    async def _get_tvid_from_link_id(self, link_id: str) -> Optional[str]:
        """
        新增：使用官方API将视频链接ID解码为tvid。
        这比解析HTML更可靠。
        """
        api_url = f"https://pcw-api.iq.com/api/decode/{link_id}?platformId=3&modeCode=intl&langCode=sg"
        try:
            response = await self.client.get(api_url)
            response.raise_for_status()
            data = response.json()
            if data.get("code") in ["A00000", "0"] and data.get("data"):
                return str(data["data"])
            else:
                self.logger.warning(f"爱奇艺: decode API 未成功返回 tvid (link_id: {link_id})。响应: {data}")
                return None
        except Exception as e:
            self.logger.error(f"爱奇艺: 调用 decode API 失败 (link_id: {link_id}): {e}", exc_info=True)
            return None

    async def _get_video_base_info(self, link_id: str) -> Optional[IqiyiHtmlVideoInfo]:
        # 修正：缓存键必须包含分集信息，以区分对同一标题的不同分集搜索
        cache_key = f"base_info_{link_id}"
        cached_info = await self._get_from_cache(cache_key)
        if cached_info is not None:
            self.logger.info(f"爱奇艺: 从缓存中命中基础信息 (link_id={link_id})")
            try:
                return IqiyiHtmlVideoInfo.model_validate(cached_info)
            except ValidationError as e:
                self.logger.error(f"爱奇艺: 缓存的基础信息 (link_id={link_id}) 验证失败。这可能是一个陈旧或损坏的缓存。")
                self.logger.error(f"导致验证失败的数据: {cached_info}")
                self.logger.error(f"Pydantic 验证错误: {e}")
                return None

        # 重构：使用新的API调用方式
        tvid = await self._get_tvid_from_link_id(link_id)
        if not tvid:
            return None

        url = f"https://pcw-api.iqiyi.com/video/video/baseinfo/{tvid}"
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            data = response.json()
            if data.get("code") != "A00000" or not data.get("data"):
                self.logger.warning(f"爱奇艺: baseinfo API 未成功返回数据 (tvid: {tvid})。响应: {data}")
                return None
            
            video_info = IqiyiHtmlVideoInfo.model_validate(data["data"])

            info_to_cache = video_info.model_dump()
            await self._set_to_cache(cache_key, info_to_cache, 'base_info_ttl_seconds', 1800)
            return video_info
        except Exception as e:
            self.logger.error(f"爱奇艺: 获取或解析 baseinfo 失败 (tvid: {tvid}): {e}", exc_info=True)
            return None

    async def _get_tv_episodes(self, album_id: int, size: int = 500) -> List[IqiyiEpisodeInfo]:
        # 这个函数被 get_episodes 调用，缓存应该在 get_episodes 层面处理
        url = f"https://pcw-api.iqiyi.com/albums/album/avlistinfo?aid={album_id}&page=1&size={size}"
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            data = IqiyiVideoResult.model_validate(response.json())
            return data.data.epsodelist if data.data else []
        except Exception as e:
            self.logger.error(f"爱奇艺: 获取剧集列表失败 (album_id: {album_id}): {e}", exc_info=True)
            return []

    async def get_episodes(self, media_id: str, target_episode_index: Optional[int] = None, db_media_type: Optional[str] = None) -> List[models.ProviderEpisodeInfo]:
        cache_key = f"episodes_{media_id}"
        # 仅当不是强制模式（即初次导入）且请求完整列表时才使用缓存
        if target_episode_index is None and db_media_type is None:
            cached_episodes = await self._get_from_cache(cache_key)
            if cached_episodes is not None:
                self.logger.info(f"爱奇艺: 从缓存中命中分集列表 (media_id={media_id})")
                return [models.ProviderEpisodeInfo.model_validate(e) for e in cached_episodes]

        base_info = await self._get_video_base_info(media_id)
        if base_info is None:
            return []

        episodes: List[IqiyiEpisodeInfo] = []

        # 决定处理模式：优先使用数据库类型，其次根据刮削信息判断
        is_movie_mode = False
        if db_media_type == "movie":
            is_movie_mode = True
            self.logger.info(f"爱奇艺: 根据数据库类型 'movie'，强制使用单集模式 (media_id={media_id})")
        elif db_media_type == "tv_series":
            is_movie_mode = False
            self.logger.info(f"爱奇艺: 根据数据库类型 'tv_series'，强制使用剧集模式 (media_id={media_id})")
        else:  # db_media_type is None, fall back to scraping logic
            # 修正：新API不一定有video_count，主要依赖channelName
            is_movie_mode = base_info.channel_name is not None and base_info.channel_name.strip() == "电影"

        if is_movie_mode:
            # 单集（电影）处理逻辑
            episode_data = {
                "tvId": base_info.tv_id or 0,
                "name": base_info.video_name,
                "order": 1,
                "playUrl": base_info.video_url
            }
            episodes.append(IqiyiEpisodeInfo.model_validate(episode_data))
        else:
            # 多集（电视剧/动漫）处理逻辑
            episodes = await self._get_tv_episodes(base_info.album_id)

            if target_episode_index:
                target_episode_from_list = next((ep for ep in episodes if ep.order == target_episode_index), None)
                if target_episode_from_list:
                    episodes = [target_episode_from_list]
                else:
                    self.logger.warning(f"爱奇艺: 目标分集 {target_episode_index} 在获取的列表中未找到 (album_id={base_info.album_id})")
                    return []

            self.logger.debug(f"爱奇艺: 正在为 {len(episodes)} 个分集并发获取真实标题...")
            tasks = [self._get_video_base_info(ep.link_id) for ep in episodes if ep.link_id]
            detailed_infos = await asyncio.gather(*tasks, return_exceptions=True)

            specific_title_map = {}
            for info in detailed_infos:
                if isinstance(info, IqiyiHtmlVideoInfo) and info.tv_id:
                    specific_title_map[info.tv_id] = info.video_name

            for ep in episodes:
                specific_title = specific_title_map.get(ep.tv_id)
                if specific_title and specific_title != ep.name:
                    self.logger.debug(f"爱奇艺: 标题替换: '{ep.name}' -> '{specific_title}'")
                    ep.name = specific_title

        provider_episodes = [
            models.ProviderEpisodeInfo(
                provider=self.provider_name,
                episodeId=str(ep.tv_id), # Use tv_id for danmaku
                title=ep.name,
                episodeIndex=ep.order,
                url=ep.play_url
            ) for ep in episodes if ep.link_id
        ]
        
        # 根据黑名单过滤分集
        if self._EPISODE_BLACKLIST_PATTERN:
            original_count = len(provider_episodes)
            provider_episodes = [ep for ep in provider_episodes if not self._EPISODE_BLACKLIST_PATTERN.search(ep.title)]
            filtered_count = original_count - len(provider_episodes)
            if filtered_count > 0:
                self.logger.info(f"Iqiyi: 根据黑名单规则过滤掉了 {filtered_count} 个分集。")

        # 仅当不是强制模式且获取完整列表时才进行缓存
        if target_episode_index is None and db_media_type is None and provider_episodes:
            episodes_to_cache = [e.model_dump() for e in provider_episodes]
            await self._set_to_cache(cache_key, episodes_to_cache, 'episodes_ttl_seconds', 1800)
        return provider_episodes

    async def _get_danmu_content_by_mat(self, tv_id: str, mat: int) -> List[IqiyiComment]:
        if len(tv_id) < 4: return []
        
        s1 = tv_id[-4:-2]
        s2 = tv_id[-2:]
        url = f"http://cmts.iqiyi.com/bullet/{s1}/{s2}/{tv_id}_300_{mat}.z"
        
        try:
            response = await self.client.get(url)
            if response.status_code == 404:
                self.logger.info(f"爱奇艺: 找不到 tvId {tv_id} 的弹幕分段 {mat}，停止获取。")
                return [] # 404 means no more segments
            response.raise_for_status()

            # 根据用户的反馈，恢复为标准的 zlib 解压方式。
            decompressed_data = zlib.decompress(response.content)

            # 增加显式的UTF-8解析器以提高健壮性
            parser = ET.XMLParser(encoding="utf-8")
            root = ET.fromstring(decompressed_data, parser=parser)
            
            comments = []
            # 关键修复：根据日志，弹幕信息在 <bulletInfo> 标签内
            for item in root.findall('.//bulletInfo'):
                content_node = item.find('content')
                show_time_node = item.find('showTime')

                # 核心字段必须存在
                if not (content_node is not None and content_node.text and show_time_node is not None and show_time_node.text):
                    continue
                
                # 安全地获取可选字段
                content_id_node = item.find('contentId')
                color_node = item.find('color')
                user_info_node = item.find('userInfo')
                uid_node = user_info_node.find('uid') if user_info_node is not None else None

                comments.append(IqiyiComment(
                    contentId=content_id_node.text if content_id_node is not None and content_id_node.text else "0",
                    content=content_node.text,
                    showTime=int(show_time_node.text),
                    color=color_node.text if color_node is not None and color_node.text else "ffffff",
                    userInfo=IqiyiUserInfo(uid=uid_node.text) if uid_node is not None and uid_node.text else None
                ))
            return comments
        except zlib.error:
            self.logger.warning(f"爱奇艺: 解压 tvId {tv_id} 的弹幕分段 {mat} 失败，文件可能为空或已损坏。")
        except ET.ParseError:
            self.logger.warning(f"爱奇艺: 解析 tvId {tv_id} 的弹幕分段 {mat} 的XML失败。")
        except Exception as e:
            self.logger.error(f"爱奇艺: 获取 tvId {tv_id} 的弹幕分段 {mat} 时出错: {e}", exc_info=True)
        
        return []

    async def get_comments(self, episode_id: str, progress_callback: Optional[Callable] = None) -> List[dict]:
        tv_id = episode_id # For iqiyi, episodeId is tvId
        all_comments = []
        
        # iqiyi danmaku is fetched in 300-second segments (5 minutes)
        # We loop through segments until we get an empty response or a 404
        for mat in range(1, 100): # Limit to 500 minutes to prevent infinite loops
            if progress_callback:
                # 爱奇艺没有总分段数，因此我们只能显示当前正在获取哪个分段
                # 进度条可以模拟一个递增的值
                progress = min(95, mat * 5) # 假设大部分视频不会超过20个分段 (100分钟)
                await progress_callback(progress, f"正在获取第 {mat} 分段")

            comments_in_mat = await self._get_danmu_content_by_mat(tv_id, mat)
            if not comments_in_mat:
                break
            all_comments.extend(comments_in_mat)
            await asyncio.sleep(0.1) # Be nice to the server

        if progress_callback:
            await progress_callback(100, "弹幕整合完成")

        return self._format_comments(all_comments)

    def _format_comments(self, comments: List[IqiyiComment]) -> List[dict]:
        if not comments:
            return []

        # 新增：按 content_id 去重
        unique_comments_map: Dict[str, IqiyiComment] = {}
        for c in comments:
            # 保留第一次出现的弹幕
            if c.content_id not in unique_comments_map:
                unique_comments_map[c.content_id] = c
        
        unique_comments = list(unique_comments_map.values())

        # 1. 按内容对弹幕进行分组
        grouped_by_content: Dict[str, List[IqiyiComment]] = defaultdict(list)
        for c in unique_comments: # 使用去重后的列表
            grouped_by_content[c.content].append(c)

        # 2. 处理重复项
        processed_comments: List[IqiyiComment] = []
        for content, group in grouped_by_content.items():
            if len(group) == 1:
                processed_comments.append(group[0])
            else:
                first_comment = min(group, key=lambda x: x.show_time)
                first_comment.content = f"{first_comment.content} X{len(group)}"
                processed_comments.append(first_comment)

        # 3. 格式化处理后的弹幕列表
        formatted = []
        for c in processed_comments:
            mode = 1 # Default scroll
            try:
                color = int(c.color, 16)
            except (ValueError, TypeError):
                color = 16777215 # Default white

            timestamp = float(c.show_time)
            p_string = f"{timestamp:.2f},{mode},{color},[{self.provider_name}]"
            formatted.append({
                "cid": c.content_id,
                "p": p_string,
                "m": c.content,
                "t": timestamp
            })
        return formatted