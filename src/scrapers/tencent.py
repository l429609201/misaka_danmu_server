import asyncio
import httpx
import aiomysql
import re
import logging
import html
import json
from typing import List, Dict, Any, Optional, Union, Callable
from pydantic import BaseModel, Field, ValidationError
from collections import defaultdict
from datetime import datetime
from ..config_manager import ConfigManager
from .base import BaseScraper, get_season_from_title
from .. import models, crud

scraper_responses_logger = logging.getLogger("scraper_responses")

# --- Pydantic 模型，用于解析腾讯API的响应 ---

# --- Models for Get Comments API ---
class TencentCommentContentStyle(BaseModel):
    color: Optional[str] = None
    position: Optional[int] = None

class TencentEpisode(BaseModel):
    vid: str = Field(..., description="分集视频ID")
    title: str = Field(..., description="分集标题")
    is_trailer: str = Field("0", alias="is_trailer")
    union_title: Optional[str] = None

class TencentComment(BaseModel):
    id: str = Field(..., description="弹幕ID")
    # API 返回的是字符串，我们直接接收字符串，在后续处理中转为数字
    time_offset: str = Field(..., description="弹幕时间偏移(毫秒)")
    content: str = Field(..., description="弹幕内容")
    # API 对普通弹幕返回空字符串 ""，对特殊弹幕返回对象。Union可以同时处理这两种情况。
    content_style: Union[TencentCommentContentStyle, str, None] = Field(None)


# --- 用于搜索API的新模型 ---
class TencentSubjectDoc(BaseModel):
    video_num: int = Field(0, alias="videoNum")

class TencentSearchVideoInfo(BaseModel):
    title: str
    year: Optional[int] = None
    type_name: str = Field(alias="typeName")
    img_url: Optional[str] = Field(None, alias="imgUrl")
    subject_doc: Optional[TencentSubjectDoc] = Field(None, alias="subjectDoc")

class TencentSearchDoc(BaseModel):
    id: str  # 这是 cid

class TencentSearchItem(BaseModel):
    video_info: Optional[TencentSearchVideoInfo] = Field(None, alias="videoInfo")
    doc: TencentSearchDoc

class TencentSearchItemList(BaseModel):
    item_list: List[TencentSearchItem] = Field(alias="itemList")

class TencentSearchData(BaseModel):
    normal_list: Optional[TencentSearchItemList] = Field(None, alias="normalList")

class TencentSearchResult(BaseModel):
    data: Optional[TencentSearchData] = None

# --- 用于搜索API的请求模型 (参考C#代码) ---
class TencentSearchRequest(BaseModel):
    query: str
    version: str = ""
    filter_value: str = Field("firstTabid=150", alias="filterValue")
    retry: int = 0
    pagenum: int = 0
    pagesize: int = 20
    query_from: int = Field(4, alias="queryFrom")
    is_need_qc: bool = Field(True, alias="isneedQc")
    ad_request_info: str = Field("", alias="adRequestInfo")
    sdk_request_info: str = Field("", alias="sdkRequestInfo")
    scene_id: int = Field(21, alias="sceneId")
    platform: str = "23"

# --- 腾讯API客户端 ---

class TencentScraper(BaseScraper):
    """
    用于从腾讯视频抓取分集信息和弹幕的客户端。
    """
    provider_name = "tencent"

    def __init__(self, pool: aiomysql.Pool, config_manager: ConfigManager):
        super().__init__(pool, config_manager)
        # 修正：使用更健壮的正则表达式来过滤非正片内容
        self._EPISODE_BLACKLIST_PATTERN = re.compile(
            r"预告|彩蛋|专访|直拍|直播回顾|加更|走心|解忧|纯享|节点|解读|揭秘|赏析|速看|资讯|访谈|番外|短片|纪录片",
            re.IGNORECASE
        )
        # 用于从标题中提取集数的正则表达式
        self._EPISODE_INDEX_PATTERN = re.compile(r"^(?:第)?(\d+)(?:集|话)?$")
        self.base_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://v.qq.com/",
        }
        # 根据C#代码，这个特定的cookie对于成功请求至关重要
        self.cookies = {"pgv_pvid": "40b67e3b06027f3d","video_platform": "2","vversion_name": "8.2.95","video_bucketid": "4","video_omgid": "0a1ff6bc9407c0b1cff86ee5d359614d"}
        # httpx.AsyncClient 是 Python 中功能强大的异步HTTP客户端，等同于 C# 中的 HttpClient
        # 此处通过 cookies 参数传入字典，httpx 会自动将其格式化为正确的 Cookie 请求头，效果与C#代码一致
        self.client = httpx.AsyncClient(headers=self.base_headers, cookies=self.cookies, timeout=20.0)

    async def close(self):
        """关闭HTTP客户端"""
        await self.client.aclose()

    async def search(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        """通过腾讯搜索API查找番剧。"""
        # 修正：缓存键必须包含分集信息，以区分对同一标题的不同分集搜索
        cache_key_suffix = f"_s{episode_info['season']}e{episode_info['episode']}" if episode_info else ""
        cache_key = f"search_{self.provider_name}_{keyword}{cache_key_suffix}"
        cached_results = await self._get_from_cache(cache_key)
        if cached_results is not None:
            self.logger.info(f"Tencent: 从缓存中命中搜索结果 '{keyword}{cache_key_suffix}'")
            return [models.ProviderSearchInfo.model_validate(r) for r in cached_results]

        url = "https://pbaccess.video.qq.com/trpc.videosearch.mobile_search.HttpMobileRecall/MbSearchHttp"
        request_model = TencentSearchRequest(query=keyword)
        payload = request_model.model_dump(by_alias=True)
        results = []
        try:
            self.logger.info(f"Tencent: 正在搜索 '{keyword}'...")
            response = await self.client.post(url, json=payload)
            if await self._should_log_responses():
                scraper_responses_logger.debug(f"Tencent Search Response (keyword='{keyword}'): {response.text}")

            response.raise_for_status()
            response_json = response.json()
            data = TencentSearchResult.model_validate(response_json)

            if data.data and data.data.normal_list:
                for item in data.data.normal_list.item_list:
                    # 新增：检查 video_info 是否存在，因为API有时会返回null
                    if not item.video_info:
                        self.logger.debug(f"Tencent: 过滤掉一个条目，因为它缺少 'videoInfo'。")
                        continue
                    # 参考C#代码，增加对年份的过滤，可以有效排除很多不相关的结果（如：资讯、短视频等）
                    if not item.video_info.year or item.video_info.year == 0:
                        self.logger.debug(f"Tencent: 过滤掉结果 '{item.video_info.title}'，因为它缺少有效的年份信息。")
                        continue

                    video_info = item.video_info
                    # 清理标题中的HTML高亮标签 (如 <em>)，并先进行HTML解码
                    unescaped_title = html.unescape(video_info.title)
                    cleaned_title = re.sub(r'<[^>]+>', '', unescaped_title)

                    # 相似度检查：确保搜索词与结果标题相关。
                    # 这是对 C# 中 .Distance() 方法的简化实现，只进行简单的包含检查。
                    if keyword.lower() not in cleaned_title.lower():
                        self.logger.debug(f"Tencent: 过滤掉结果 '{cleaned_title}'，因为它与搜索词 '{keyword}' 不直接相关。")
                        continue

                    # 将腾讯的类型映射到我们内部的类型
                    media_type = "movie" if "电影" in video_info.type_name else "tv_series"

                    # 新增：提取总集数
                    episode_count = None
                    if video_info.subject_doc:
                        episode_count = video_info.subject_doc.video_num

                    # 新增：如果搜索时指定了集数，则将其附加到结果中
                    current_episode = episode_info.get("episode") if episode_info else None

                    # 在返回前统一冒号
                    final_title = cleaned_title.replace(":", "：")

                    provider_search_info = models.ProviderSearchInfo(
                        provider=self.provider_name,
                        mediaId=item.doc.id,
                        title=final_title,
                        type=media_type,
                        season=get_season_from_title(final_title),
                        year=video_info.year,
                        imageUrl=video_info.img_url,
                        episodeCount=episode_count,
                        currentEpisodeIndex=current_episode
                    )
                    self.logger.debug(f"Tencent: 创建的 ProviderSearchInfo: {provider_search_info.model_dump_json(indent=2)}")
                    results.append(provider_search_info)
        except httpx.HTTPStatusError as e:
            self.logger.error(f"搜索请求失败: {e}")
        except (ValidationError, KeyError) as e:
            self.logger.error(f"解析搜索结果失败: {e}", exc_info=True)
        
        self.logger.info(f"Tencent: 搜索 '{keyword}' 完成，找到 {len(results)} 个有效结果。")
        if results:
            log_results = "\n".join([f"  - {r.title} (ID: {r.mediaId}, 类型: {r.type}, 年份: {r.year or 'N/A'})" for r in results])
            self.logger.info(f"Tencent: 搜索结果列表:\n{log_results}")
        results_to_cache = [r.model_dump() for r in results]
        await self._set_to_cache(cache_key, results_to_cache, 'search_ttl_seconds', 300)
        return results

    def _get_episode_index_from_title(self, title: str) -> Optional[int]:
        """
        从分集标题（如 "01", "第01集"）中解析出集数。
        """
        if not title:
            return None
        match = self._EPISODE_INDEX_PATTERN.match(title.strip())
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                return None
        return None

    async def _internal_get_episodes(self, cid: str) -> List[TencentEpisode]:
        """
        获取指定cid的所有分集列表。
        处理了腾讯视频复杂的分页逻辑。
        """
        # 仅当请求完整列表时才使用缓存
        cache_key = f"episodes_{cid}"
        cached_episodes = await self._get_from_cache(cache_key)
        if cached_episodes is not None:
            self.logger.info(f"Tencent: 从缓存中命中分集列表 (cid={cid})")
            return [TencentEpisode.model_validate(e) for e in cached_episodes]

        url = "https://pbaccess.video.qq.com/trpc.universal_backend_service.page_server_rpc.PageServer/GetPageData?video_appid=3000010&vplatform=2"
        all_episodes: Dict[str, TencentEpisode] = {}
        # 采用C#代码中更可靠的分页逻辑
        page_size = 100
        begin_num = 1
        page_context = ""  # 首次请求为空
        last_vid_of_page = ""
    
        self.logger.info(f"开始为 cid='{cid}' 获取分集列表...")
    
        while True:
            payload = {
                "pageParams": {
                    "cid": cid,
                    "page_type": "detail_operation",
                    "page_id": "vsite_episode_list",
                    "id_type": "1",
                    "page_size": str(page_size),
                    "lid": "0",
                    "req_from": "web_mobile",
                    "page_context": page_context,
                },
            }
            # 重置下一页的上下文，只有在需要时才会被重新赋值
            next_page_context = ""
    
            try:
                self.logger.debug(f"请求分集列表 (cid={cid}), PageContext='{page_context}'")
                response = await self.client.post(url, json=payload)
                if await self._should_log_responses():
                    scraper_responses_logger.debug(f"Tencent Episodes Response (cid='{cid}', page_context='{page_context}'): {response.text}")
                response.raise_for_status()
                data = response.json()
    
                # --- 健壮的解析逻辑：合并所有模块的分集列表 ---
                all_item_datas_from_page = []
                module_list_datas = data.get("data", {}).get("module_list_datas", [])
    
                for module_list_data in module_list_datas:
                    module_datas = module_list_data.get("module_datas", [])
                    for module_data in module_datas:
                        item_data_lists = module_data.get("item_data_lists", {})
                        found_items = item_data_lists.get("item_datas")
                        if found_items:
                            all_item_datas_from_page.extend(found_items)
                # --- 解析逻辑结束 ---
    
                if not all_item_datas_from_page:
                    self.logger.warning(f"cid='{cid}': 在响应中未找到任何分集数据。")
                    self.logger.warning(f"来自腾讯的原始响应 (cid={cid}): {data}")
                    break
    
                new_episodes_found = 0
                current_page_vids = []
                for item in all_item_datas_from_page:
                    params = item.get("item_params", {})
                    if not params.get("vid"):
                        continue
    
                    episode = TencentEpisode.model_validate(params)
    
                    # 过滤非正片内容
                    title_to_check = episode.union_title or episode.title
                    is_junk = False
                    reason = ""
                    if episode.is_trailer == "1":
                        is_junk = True
                        reason = "is_trailer flag is set"
                    elif self._EPISODE_BLACKLIST_PATTERN.search(title_to_check):
                        is_junk = True
                        reason = "title matches blacklist pattern"
                    elif episode.union_title and "预告" in episode.union_title:
                        is_junk = True
                        reason = "union_title keyword '预告'"
                    if is_junk:
                        self.logger.debug(f"Tencent: 过滤掉非正片内容 '{episode.title}' (vid: {episode.vid}), 原因: {reason}")
                    elif episode.vid not in all_episodes:
                        all_episodes[episode.vid] = episode
                        new_episodes_found += 1
    
                    current_page_vids.append(episode.vid)
    
                self.logger.debug(
                    f"cid='{cid}': 当前页获取 {len(all_item_datas_from_page)} 个项目，新增 {new_episodes_found} 个有效分集。当前总数: {len(all_episodes)}"
                )
    
                # 检查是否需要翻页，并防止因API返回重复数据导致的死循环
                if not current_page_vids or current_page_vids[-1] == last_vid_of_page:
                    self.logger.info(f"cid='{cid}': 已到达最后一页或检测到重复分页数据，停止翻页。")
                    break
    
                last_vid_of_page = current_page_vids[-1]
    
                # 构造下一页的上下文
                if len(all_item_datas_from_page) >= page_size:
                    begin_num += page_size
                    end_num = begin_num + page_size - 1
                    next_page_context = f"episode_begin={begin_num}&episode_end={end_num}&episode_step={page_size}"
    
                page_context = next_page_context
                if not page_context:
                    break  # 没有下一页了，退出循环
    
                await asyncio.sleep(0.5)  # 礼貌性等待
    
            except httpx.HTTPStatusError as e:
                self.logger.error(f"请求分集列表失败 (cid={cid}): {e}")
                self.logger.debug(f"失败响应内容: {e.response.text}")
                break
            except (KeyError, IndexError, ValidationError) as e:
                self.logger.error(f"解析分集列表JSON失败 (cid={cid}): {e}", exc_info=True)
                if 'data' in locals():
                    self.logger.debug(f"导致解析失败的JSON数据: {data}")
                break
    
        final_episodes = list(all_episodes.values())
        self.logger.info(f"分集列表获取完成 (cid={cid})，去重后共 {len(final_episodes)} 个。")
    
        episodes_to_cache = [e.model_dump() for e in final_episodes]
        await self._set_to_cache(cache_key, episodes_to_cache, 'episodes_ttl_seconds', 1800)
        # 某些综艺节目可能会返回重复的剧集，这里进行去重
        return final_episodes

    async def get_episodes(self, media_id: str, target_episode_index: Optional[int] = None, db_media_type: Optional[str] = None) -> List[models.ProviderEpisodeInfo]:
        """
        获取指定cid的所有分集列表。
        media_id 对于腾讯来说就是 cid。
        """
        # 腾讯的逻辑不区分电影和电视剧，都是从一个cid获取列表，
        # 所以db_media_type在这里用不上，但为了接口统一还是保留参数。
        tencent_episodes = await self._internal_get_episodes(media_id)
        
        all_provider_episodes = []
        for i, ep in enumerate(tencent_episodes):
            # 核心修正：优先从标题解析集数，如果失败则使用 enumerate 的索引作为后备
            episode_index = self._get_episode_index_from_title(ep.title)
            if episode_index is None:
                self.logger.warning(f"无法从标题 '{ep.title}' 解析集数，将使用顺序索引 {i+1} 作为后备。")
                episode_index = i + 1

            # 智能选择标题：优先使用更完整的 union_title，否则使用原始 title
            display_title = ep.union_title if ep.union_title and ep.union_title != ep.title else ep.title
            
            all_provider_episodes.append(models.ProviderEpisodeInfo(
                provider=self.provider_name,
                episodeId=ep.vid,
                title=display_title,
                episodeIndex=episode_index,
                url=f"https://v.qq.com/x/cover/{media_id}/{ep.vid}.html"
            ))

        # 关键步骤：在返回前，根据我们解析出的真实集数进行排序
        all_provider_episodes.sort(key=lambda x: x.episodeIndex)

        # Apply custom blacklist from config
        blacklist_pattern = await self.get_episode_blacklist_pattern()
        if blacklist_pattern:
            original_count = len(all_provider_episodes)
            all_provider_episodes = [ep for ep in all_provider_episodes if not blacklist_pattern.search(ep.title)]
            filtered_count = original_count - len(all_provider_episodes)
            if filtered_count > 0:
                self.logger.info(f"Tencent: 根据自定义黑名单规则过滤掉了 {filtered_count} 个分集。")

        # 如果指定了目标，则只返回目标分集
        if target_episode_index is not None:
            target_episode = next((ep for ep in all_provider_episodes if ep.episodeIndex == target_episode_index), None)
            return [target_episode] if target_episode else []
        return all_provider_episodes

    async def _internal_get_comments(self, vid: str, progress_callback: Optional[Callable] = None) -> List[TencentComment]:
        """
        获取指定vid的所有弹幕。
        分两步：先获取弹幕分段索引，再逐个获取分段内容。
        """
        all_comments: List[TencentComment] = []
        # 1. 获取弹幕分段索引
        index_url = f"https://dm.video.qq.com/barrage/base/{vid}"
        try:
            response = await self.client.get(index_url)
            if await self._should_log_responses():
                scraper_responses_logger.debug(f"Tencent Danmaku Index Response (vid={vid}): {response.text}")
            response.raise_for_status()
            index_data = response.json()
            segment_index = index_data.get("segment_index", {})
            if not segment_index: # 如果视频没有弹幕，这里会是空的
                self.logger.info(f"vid='{vid}' 没有找到弹幕分段索引。")
                return []
        except Exception as e:
            self.logger.error(f"获取弹幕索引失败 (vid={vid}): {e}", exc_info=True)
            return []

        # 2. 遍历分段，获取弹幕内容
        total_segments = len(segment_index)
        self.logger.debug(f"为 vid='{vid}' 找到 {total_segments} 个弹幕分段，开始获取...")
        if progress_callback:
            await progress_callback(5, f"找到 {total_segments} 个弹幕分段")

        # 与C#代码不同，这里我们直接遍历所有分段以获取全部弹幕，而不是抽样
        # 按key（时间戳）排序，确保弹幕顺序正确
        sorted_keys = sorted(segment_index.keys(), key=int)
        for i, key in enumerate(sorted_keys):
            segment = segment_index[key]
            segment_name = segment.get("segment_name")
            if not segment_name:
                continue
            
            if progress_callback:
                # 5%用于获取索引，90%用于下载，5%用于格式化
                progress = 5 + int(((i + 1) / total_segments) * 90)
                await progress_callback(progress, f"正在下载分段 {i+1}/{total_segments}")

            segment_url = f"https://dm.video.qq.com/barrage/segment/{vid}/{segment_name}"
            try:
                response = await self.client.get(segment_url)
                if await self._should_log_responses():
                    scraper_responses_logger.debug(f"Tencent Danmaku Segment Response (vid={vid}, segment={segment_name}): status={response.status_code}")
                response.raise_for_status()
                comment_data = response.json()
                
                barrage_list = comment_data.get("barrage_list", [])
                for comment_item in barrage_list:
                    try:
                        all_comments.append(TencentComment.model_validate(comment_item))
                    except ValidationError as e:
                        # 腾讯的弹幕列表里有时会混入非弹幕数据（如广告、推荐等），这些数据结构不同
                        # 我们在这里捕获验证错误，记录并跳过这些无效数据，以保证程序健壮性
                        self.logger.warning(f"跳过一个无效的弹幕项目，因为它不符合预期的格式。原始数据: {comment_item}, 错误: {e}")
                
                await asyncio.sleep(0.2) # 礼貌性等待

            except Exception as e:
                self.logger.error(f"获取分段 {segment_name} 失败 (vid={vid}): {e}", exc_info=True)
                continue
        
        if progress_callback:
            await progress_callback(100, "弹幕整合完成")

        self.logger.info(f"vid='{vid}' 弹幕获取完成，共 {len(all_comments)} 条。")
        return all_comments

    async def get_comments(self, episode_id: str, progress_callback: Optional[Callable] = None) -> List[dict]:
        """
        获取指定vid的所有弹幕。
        episode_id 对于腾讯来说就是 vid。
        返回一个字典列表，可直接用于批量插入数据库。
        """
        tencent_comments = await self._internal_get_comments(episode_id, progress_callback)

        if not tencent_comments:
            return []

        # 新增：按弹幕ID去重
        unique_tencent_comments = list({c.id: c for c in tencent_comments}.values())

        # 1. 按内容对弹幕进行分组
        grouped_by_content: Dict[str, List[TencentComment]] = defaultdict(list)
        for c in unique_tencent_comments: # 使用去重后的列表
            grouped_by_content[c.content].append(c)

        # 2. 处理重复项
        processed_comments: List[TencentComment] = []
        for content, group in grouped_by_content.items():
            if len(group) == 1:
                processed_comments.append(group[0])
            else:
                first_comment = min(group, key=lambda x: int(x.time_offset))
                first_comment.content = f"{first_comment.content} X{len(group)}"
                processed_comments.append(first_comment)

        # 3. 格式化处理后的弹幕列表
        formatted_comments = []
        for c in processed_comments:
            # 默认值
            mode = 1  # 滚动
            color = 16777215  # 白色

            # 增强的样式处理：只有当 content_style 是一个真正的对象时才处理
            if isinstance(c.content_style, TencentCommentContentStyle):
                if c.content_style.position == 2:
                    mode = 5  # 顶部
                elif c.content_style.position == 3:
                    mode = 4  # 底部
                
                if c.content_style.color:
                    try:
                        # 修正：腾讯的颜色值是十进制字符串，直接转换为整数
                        color = int(c.content_style.color)
                    except (ValueError, TypeError):
                        pass # 转换失败则使用默认白色
            
            # 将字符串类型的 time_offset 转为浮点数秒
            timestamp = int(c.time_offset) / 1000.0
            # 格式: 时间,模式,颜色,[来源]
            p_string = f"{timestamp:.2f},{mode},{color},[{self.provider_name}]"
            formatted_comments.append({"cid": c.id, "p": p_string, "m": c.content, "t": round(timestamp, 2)})

        return formatted_comments

    async def get_vid_from_url(self, url: str) -> Optional[str]:
        """从腾讯视频URL中提取 vid。"""
        # 腾讯视频的URL格式多样，但通常vid是路径的最后一部分
        match = re.search(r'/([a-zA-Z0-9]+)\.html', url)
        if match:
            vid = match.group(1)
            self.logger.info(f"Tencent: 从URL {url} 解析到 vid: {vid}")
            return vid
        self.logger.warning(f"Tencent: 无法从URL中解析出 vid: {url}")
        return None