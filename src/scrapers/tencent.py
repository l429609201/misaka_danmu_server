import asyncio
import httpx
import re
import logging
import html
import json
from typing import List, Dict, Any, Optional, Union, Callable
from pydantic import BaseModel, Field, ValidationError
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
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
    handled_domains = ["v.qq.com"]
    referer = "https://v.qq.com/"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager):
        super().__init__(session_factory, config_manager)
        # 修正：使用更健壮的正则表达式来过滤非正片内容
        # 合并了用户脚本中的关键词，并增加了对 "纯享版"、"会员版" 等常见衍生内容的过滤
        self._EPISODE_BLACKLIST_PATTERN = re.compile(
            r"预告|彩蛋|专访|直拍|直播回顾|加更|走心|解忧|纯享|节点|解读|揭秘|赏析|速看|资讯|访谈|番外|短片|纪录片|"
            r"花絮|看点|预告片|精彩|NG|特辑|菜单|片花|首映礼|宣传片|未删减|剪辑版|MV|主题曲|片尾曲|OST|纯享版|会员版|独家版|未播|抢先看|精选合集",
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

    async def _search_desktop_api(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        """通过腾讯桌面搜索API查找番剧。"""
        url = "https://pbaccess.video.qq.com/trpc.videosearch.mobile_search.HttpMobileRecall/MbSearchHttp"
        request_model = TencentSearchRequest(query=keyword)
        payload = request_model.model_dump(by_alias=True)
        results = []
        try:
            self.logger.info(f"Tencent (桌面API): 正在搜索 '{keyword}'...")
            response = await self.client.post(url, json=payload)
            if await self._should_log_responses():
                scraper_responses_logger.debug(f"Tencent Desktop Search Response (keyword='{keyword}'): {response.text}")

            response.raise_for_status()
            response_json = response.json()
            data = TencentSearchResult.model_validate(response_json)

            if data.data and data.data.normal_list:
                for item in data.data.normal_list.item_list:
                    if not item.video_info: continue
                    if not item.video_info.year or item.video_info.year == 0: continue

                    video_info = item.video_info
                    unescaped_title = html.unescape(video_info.title)
                    cleaned_title = re.sub(r'<[^>]+>', '', unescaped_title)

                    if keyword.lower() not in cleaned_title.lower(): continue

                    media_type = "movie" if "电影" in video_info.type_name else "tv_series"
                    episode_count = video_info.subject_doc.video_num if video_info.subject_doc else None
                    current_episode = episode_info.get("episode") if episode_info else None
                    final_title = cleaned_title.replace(":", "：")

                    provider_search_info = models.ProviderSearchInfo(
                        provider=self.provider_name, mediaId=item.doc.id, title=final_title,
                        type=media_type, season=get_season_from_title(final_title),
                        year=video_info.year, imageUrl=video_info.img_url,
                        episodeCount=episode_count, currentEpisodeIndex=current_episode
                    )
                    results.append(provider_search_info)
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Tencent (桌面API): 搜索请求失败: {e}")
        except (ValidationError, KeyError) as e:
            self.logger.error(f"Tencent (桌面API): 解析搜索结果失败: {e}", exc_info=True)
        return results

    async def _search_mobile_api(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        """通过腾讯移动端搜索API查找番剧。"""
        url = "https://pbaccess.video.qq.com/trpc.videosearch.mobile_search.HttpMobileRecall/MbSearchHttp"
        request_model = TencentSearchRequest(query=keyword)
        payload = request_model.model_dump(by_alias=True)
        headers = self.base_headers.copy()
        headers['User-Agent'] = 'live4iphoneRel/9.01.46 (iPhone; iOS 18.5; Scale/3.00)'
        
        results = []
        try:
            self.logger.info(f"Tencent (移动API): 正在搜索 '{keyword}'...")
            response = await self.client.post(url, json=payload, headers=headers)
            if await self._should_log_responses():
                scraper_responses_logger.debug(f"Tencent Mobile Search Response (keyword='{keyword}'): {response.text}")

            response.raise_for_status()
            response_json = response.json()
            data = TencentSearchResult.model_validate(response_json)

            if data.data and data.data.normal_list:
                for item in data.data.normal_list.item_list:
                    if not item.video_info: continue
                    if not item.video_info.year or item.video_info.year == 0: continue

                    video_info = item.video_info
                    unescaped_title = html.unescape(video_info.title)
                    cleaned_title = re.sub(r'<[^>]+>', '', unescaped_title)

                    if keyword.lower() not in cleaned_title.lower(): continue

                    media_type = "movie" if "电影" in video_info.type_name else "tv_series"
                    episode_count = video_info.subject_doc.video_num if video_info.subject_doc else None
                    current_episode = episode_info.get("episode") if episode_info else None
                    final_title = cleaned_title.replace(":", "：")

                    provider_search_info = models.ProviderSearchInfo(
                        provider=self.provider_name, mediaId=item.doc.id, title=final_title,
                        type=media_type, season=get_season_from_title(final_title),
                        year=video_info.year, imageUrl=video_info.img_url,
                        episodeCount=episode_count, currentEpisodeIndex=current_episode
                    )
                    results.append(provider_search_info)
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Tencent (移动API): 搜索请求失败: {e}")
        except (ValidationError, KeyError) as e:
            self.logger.error(f"Tencent (移动API): 解析搜索结果失败: {e}", exc_info=True)
        return results

    async def search(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        """并发执行桌面端和移动端搜索，并合并去重结果。"""

        desktop_task = self._search_desktop_api(keyword, episode_info)
        mobile_task = self._search_mobile_api(keyword, episode_info)
        
        results_lists = await asyncio.gather(desktop_task, mobile_task, return_exceptions=True)
        
        all_results = []
        for i, res_list in enumerate(results_lists):
            api_name = "桌面API" if i == 0 else "移动API"
            if isinstance(res_list, list):
                all_results.extend(res_list)
            elif isinstance(res_list, Exception):
                self.logger.error(f"Tencent ({api_name}): 搜索子任务失败: {res_list}", exc_info=True)

        # 基于 mediaId 去重
        unique_results = list({item.mediaId: item for item in all_results}.values())

        self.logger.info(f"Tencent (合并): 搜索 '{keyword}' 完成，找到 {len(unique_results)} 个唯一结果。")
        if unique_results:
            log_results = "\n".join([f"  - {r.title} (ID: {r.mediaId}, 类型: {r.type}, 年份: {r.year or 'N/A'})" for r in unique_results])
            self.logger.info(f"Tencent (合并): 搜索结果列表:\n{log_results}")

        return unique_results

    async def get_info_from_url(self, url: str) -> Optional[models.ProviderSearchInfo]:
        """从腾讯视频URL中提取作品信息。"""
        self.logger.info(f"Tencent: 正在从URL提取信息: {url}")
        cid_match = re.search(r'/cover/([^/]+?)(/|\.html|$)', url)
        if not cid_match:
            self.logger.warning(f"Tencent: 无法从URL中解析出cid: {url}")
            return None
        
        cid = cid_match.group(1)
        
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            html_content = response.text

            title_match = re.search(r'<title>(.*?)</title>', html_content)
            title = title_match.group(1).split('-')[0].strip() if title_match else "未知标题"

            # 尝试从页面JSON中获取更精确的信息
            json_match = re.search(r'window\.video_next_list\s*=\s*({.*?});', html_content)
            media_type = "tv_series" # 默认
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    if data.get("type") == "1": media_type = "movie"
                except json.JSONDecodeError:
                    pass

            return models.ProviderSearchInfo(
                provider=self.provider_name, mediaId=cid, title=title, type=media_type, season=get_season_from_title(title)
            )
        except Exception as e:
            self.logger.error(f"Tencent: 从URL '{url}' 提取信息失败: {e}", exc_info=True)
            return None

    async def get_episodes(self, media_id: str, target_episode_index: Optional[int] = None, db_media_type: Optional[str] = None) -> List[models.ProviderEpisodeInfo]:
        """
        获取分集列表。
        优先使用新的分页逻辑 (v2)，如果失败或未返回结果，则回退到旧的逻辑 (v1)。
        """
        # 方案1 (主): 使用新的、更健壮的分页逻辑
        # 修正：将过滤逻辑移入 _process_and_format_tencent_episodes，确保所有获取路径都经过处理
        try:
            episodes_v2 = await self._get_episodes_v2(media_id, db_media_type)
            if episodes_v2:
                self.logger.info("Tencent: 已通过新版分页API成功获取分集。")
                if target_episode_index:
                    return [ep for ep in episodes_v2 if ep.episodeIndex == target_episode_index]
                return episodes_v2
        except Exception as e:
            self.logger.warning(f"Tencent: 新版分页API获取分集失败，将尝试备用方案。错误: {e}", exc_info=True)

        # 方案2 (备): 回退到旧的分页逻辑
        self.logger.warning("Tencent: 新版API失败，正在回退到旧版分页API获取分集...")
        episodes_v1 = await self._get_episodes_v1(media_id, db_media_type)
        if target_episode_index:
            return [ep for ep in episodes_v1 if ep.episodeIndex == target_episode_index]
        return episodes_v1

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

    async def _get_episodes_v2(self, cid: str, db_media_type: Optional[str]) -> List[models.ProviderEpisodeInfo]:
        """
        新的分集获取逻辑，基于用户提供的脚本，具有更智能的分页策略。
        """
        cache_key = f"episodes_v2_{cid}"
        cached_episodes = await self._get_from_cache(cache_key)
        if cached_episodes is not None:
            self.logger.info(f"Tencent (v2): 从缓存中命中分集列表 (cid={cid})")
            return [models.ProviderEpisodeInfo.model_validate(e) for e in cached_episodes]

        url = "https://pbaccess.video.qq.com/trpc.universal_backend_service.page_server_rpc.PageServer/GetPageData?video_appid=3000010&vplatform=2"
        all_episodes: Dict[str, TencentEpisode] = {}
        
        items_per_page = 30
        # 根据内容类型设置不同的最大页数，以应对《仙逆》等长篇动漫
        max_pages = 4
        if db_media_type == 'tv_series':
            max_pages = 15 # 动漫等长剧集可以尝试更多页
        
        self.logger.info(f"开始为 cid='{cid}' 获取分集列表 (v2)...")
        
        for page_num in range(max_pages):
            page_context = f"cid={cid}&detail_page_type=1&id_type=1&is_skp_style=false&lid=&mvl_strategy_id=&order=&req_from=web_vsite&req_from_second_type=detail_operation&req_type=0&should_apply_tab_in_player=false&should_apply_tab_in_sub_page=false&show_all_episode=false&tab_data_key=lid%3D%26cid%3D{cid}&un_strategy_id=ea35cb94195c48c091172a047da3e761"
            payload = {
                "page_params": {
                    "cid": cid, "page_type": "detail_operation", "page_id": "vsite_episode_list_search",
                    "id_type": "1", "page_size": str(items_per_page), "lid": "", "req_from": "web_vsite",
                    "page_context": page_context, "page_num": str(page_num)
                }
            }
            
            try:
                self.logger.debug(f"请求分集列表 (cid={cid}), Page={page_num}")
                response = await self.client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()

                module_list_datas = data.get("data", {}).get("module_list_datas", [])
                new_episodes_this_page = 0
                for module_data in module_list_datas:
                    for module in module_data.get("module_datas", []):
                        items = module.get("item_data_lists", {}).get("item_datas", [])
                        for item in items:
                            params = item.get("item_params", {})
                            if not params.get("vid"): continue
                            episode = TencentEpisode.model_validate(params)
                            if episode.vid not in all_episodes:
                                all_episodes[episode.vid] = episode
                                new_episodes_this_page += 1
                
                self.logger.info(f"cid='{cid}': 第 {page_num + 1} 页找到 {new_episodes_this_page} 个新分集。")
                if new_episodes_this_page == 0 and page_num > 0:
                    self.logger.info(f"cid='{cid}': 第 {page_num + 1} 页未找到新分集，停止分页。")
                    break
                
                await asyncio.sleep(0.5)
            except Exception as e:
                self.logger.error(f"获取第 {page_num + 1} 页分集时出错: {e}")
                break
        
        final_episodes = await self._process_and_format_tencent_episodes(list(all_episodes.values()), db_media_type)
        await self._set_to_cache(cache_key, [e.model_dump() for e in final_episodes], 'episodes_ttl_seconds', 1800)
        return final_episodes

    async def _get_episodes_v1(self, media_id: str, db_media_type: Optional[str] = None) -> List[models.ProviderEpisodeInfo]:
        """旧版分集获取逻辑，作为备用方案。"""
        tencent_episodes = await self._internal_get_episodes_v1(media_id)
        return await self._process_and_format_tencent_episodes(tencent_episodes, db_media_type)

    async def _process_and_format_tencent_episodes(self, tencent_episodes: List[TencentEpisode], db_media_type: Optional[str]) -> List[models.ProviderEpisodeInfo]:
        """
        将原始腾讯分集列表处理并格式化为通用格式。
        新增：过滤非正片内容，并为综艺和普通剧集应用不同的排序和重编号逻辑。
        """
        # 1. 初步过滤
        pre_filtered = []
        for ep in tencent_episodes:
            if ep.is_trailer == "1":
                continue
            title_to_check = ep.union_title or ep.title
            if self._EPISODE_BLACKLIST_PATTERN.search(title_to_check):
                continue
            pre_filtered.append(ep)

        # 2. 判断是否为综艺节目
        is_variety_show = False
        if db_media_type == 'tv_series' and any("期" in (ep.union_title or ep.title) for ep in pre_filtered):
            is_variety_show = True

        # 3. 根据类型进行处理
        if is_variety_show:
            self.logger.info("检测到综艺节目，正在应用特殊排序和过滤规则...")
            episode_infos = []
            for ep in pre_filtered:
                title = ep.union_title or ep.title
                qi_match = re.search(r'第(\d+)期', title)
                if not qi_match: continue

                updown_match = re.search(r'第(\d+)期([上下])', title)
                qi_num = int(qi_match.group(1))
                part = updown_match.group(2) if updown_match else ''
                episode_infos.append({'ep': ep, 'qi_num': qi_num, 'part': part})

            def sort_key(e):
                part_order = {'上': 1, '': 2, '下': 3}
                return (e['qi_num'], part_order.get(e['part'], 99))
            
            episode_infos.sort(key=sort_key)
            
            # 重新编号并格式化
            episodes_to_format = [info['ep'] for info in episode_infos]
        else:
            # 普通电视剧/动漫处理
            def sort_key_regular(ep: TencentEpisode):
                title = ep.union_title or ep.title
                match = re.search(r'第(\d+)集', title)
                if match: return int(match.group(1))
                match = re.match(r'(\d+)', title)
                if match: return int(match.group(1))
                return float('inf') # 没有数字的排在最后

            episodes_to_format = sorted(pre_filtered, key=sort_key_regular)

        # 4. 应用自定义黑名单并最终格式化
        final_episodes = []
        custom_blacklist_pattern = await self.get_episode_blacklist_pattern()

        for i, ep in enumerate(episodes_to_format):
            display_title = ep.union_title or ep.title
            if custom_blacklist_pattern and custom_blacklist_pattern.search(display_title):
                continue
            
            final_episodes.append(models.ProviderEpisodeInfo(
                provider=self.provider_name,
                episodeId=ep.vid,
                title=display_title,
                episodeIndex=i + 1, # 关键：使用连续索引
                url=f"https://v.qq.com/x/cover/{ep.vid}.html"
            ))

        return final_episodes

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

    async def _internal_get_episodes_v1(self, cid: str) -> List[TencentEpisode]:
        """旧版分集获取逻辑的内部实现。"""
        cache_key = f"episodes_v1_{cid}"
        cached_episodes = await self._get_from_cache(cache_key)
        if cached_episodes is not None:
            self.logger.info(f"Tencent (v1): 从缓存中命中分集列表 (cid={cid})")
            return [TencentEpisode.model_validate(e) for e in cached_episodes]

        url = "https://pbaccess.video.qq.com/trpc.universal_backend_service.page_server_rpc.PageServer/GetPageData?video_appid=3000010&vplatform=2"
        all_episodes: Dict[str, TencentEpisode] = {}
        page_size = 100
        page_context = ""
        last_vid_of_page = ""
    
        self.logger.info(f"开始为 cid='{cid}' 获取分集列表 (v1)...")
    
        while True:
            payload = {
                "pageParams": {
                    "cid": cid, "page_type": "detail_operation", "page_id": "vsite_episode_list",
                    "id_type": "1", "page_size": str(page_size), "lid": "0", "req_from": "web_mobile",
                    "page_context": page_context,
                },
            }
            
            try:
                self.logger.debug(f"请求分集列表 (cid={cid}), PageContext='{page_context}'")
                response = await self.client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
    
                all_item_datas_from_page = []
                for module_list_data in data.get("data", {}).get("module_list_datas", []):
                    for module_data in module_list_data.get("module_datas", []):
                        item_data_lists = module_data.get("item_data_lists", {})
                        if found_items := item_data_lists.get("item_datas"):
                            all_item_datas_from_page.extend(found_items)
    
                if not all_item_datas_from_page:
                    break
    
                current_page_vids = []
                for item in all_item_datas_from_page:
                    params = item.get("item_params", {})
                    if not params.get("vid"): continue
                    episode = TencentEpisode.model_validate(params)
                    if episode.vid not in all_episodes:
                        all_episodes[episode.vid] = episode
                    current_page_vids.append(episode.vid)
    
                if not current_page_vids or current_page_vids[-1] == last_vid_of_page:
                    break
    
                last_vid_of_page = current_page_vids[-1]
                
                begin_num = len(all_episodes) + 1
                end_num = begin_num + page_size - 1
                page_context = f"episode_begin={begin_num}&episode_end={end_num}&episode_step={page_size}"
                
                await asyncio.sleep(0.5)
    
            except Exception as e:
                self.logger.error(f"请求分集列表失败 (v1, cid={cid}): {e}", exc_info=True)
                break
    
        final_episodes = list(all_episodes.values())
        await self._set_to_cache(cache_key, [e.model_dump() for e in final_episodes], 'episodes_ttl_seconds', 1800)
        return final_episodes

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

    async def get_id_from_url(self, url: str) -> Optional[str]:
        """从腾讯视频URL中提取 vid。"""
        # 腾讯视频的URL格式多样，但通常vid是路径的最后一部分
        match = re.search(r'/([a-zA-Z0-9]+)\.html', url)
        if match:
            vid = match.group(1)
            self.logger.info(f"Tencent: 从URL {url} 解析到 vid: {vid}")
            return vid
        self.logger.warning(f"Tencent: 无法从URL中解析出 vid: {url}")
        return None

    def format_episode_id_for_comments(self, provider_episode_id: Any) -> str:
        """For Tencent, the episode ID is a simple string (vid), so no formatting is needed."""
        return str(provider_episode_id)
