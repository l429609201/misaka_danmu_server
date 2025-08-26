import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Union
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup # type: ignore
from pydantic import BaseModel, Field, ValidationError

from .. import models
from ..config_manager import ConfigManager
from .base import BaseMetadataSource
from ..scrapers.base import get_season_from_title

logger = logging.getLogger(__name__)

# --- Pydantic Models for 360 API ---

class So360SearchResultItem(BaseModel):
    id: str
    en_id: Optional[str] = Field(None, alias="en_id")
    title: str = Field(alias="titleTxt")
    year: Optional[str] = None
    cover: Optional[str] = None
    cat_id: Optional[str] = Field(None, alias="cat_id")
    cat_name: Optional[str] = Field(None, alias="cat_name")
    playlinks: Dict[str, Any] = Field(default_factory=dict)
    playlinks_year: Optional[Dict[str, List[int]]] = Field(None, alias="playlinks_year")
    years: Optional[List[int]] = None
    alias: Optional[List[str]] = None

class So360SearchRes(BaseModel):
    rows: List[So360SearchResultItem] = Field(default_factory=list)

class So360SearchResult(BaseModel):
    longData: Optional[So360SearchRes] = Field(None, alias="longData")

class So360SearchResponse(BaseModel):
    data: Optional[So360SearchResult] = None

class So360CoverInfo(BaseModel):
    id: str
    title: str
    sub_title: Optional[str] = Field(None, alias="sub_title")
    description: Optional[str] = None
    cover: Optional[str] = None
    year: Optional[str] = None
    cat: Optional[str] = None  # e.g., "动漫,日本"

    @property
    def media_type(self) -> str:
        if not self.cat:
            return "other"
        if "电影" in self.cat:
            return "movie"
        return "tv_series"

# --- Main Scraper Class ---

class So360MetadataSource(BaseMetadataSource):
    provider_name = "360"

    def __init__(self, session_factory, config_manager: ConfigManager, scraper_manager):
        super().__init__(session_factory, config_manager, scraper_manager)
        self.api_base_url = "https://api.so.360kan.com"
        self.web_base_url = "https://www.360kan.com"
        # 修正：硬编码一组更完整的有效Cookie，以确保API请求成功
        hardcoded_cookies = {
            '__guid': '26972607.2949894437869698600.1752640253092.913',
            'refer_scene': '47007',
            '__huid': '11da4Vxk54oFVy89kXmOuuvPhPxzN45efwa8EHQR4I8Tg%3D',
            '___sid': '26972607.3930629777557762600.1752655408731.65',
            '__DC_gid': '26972607.192430250.1752640253137.1752656674152.17',
            'monitor_count': '12',
        }
        self.client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
            cookies=hardcoded_cookies,
            timeout=20.0,
        )

    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        search_url = f"{self.api_base_url}/index"
        params = {
            'force_v': '1',
            "kw": keyword,
            'from': '',
            'pageno': '1',
            'v_ap': '1',
            'tab': 'all',
            'cb': '__jp0'
        }
        try:
            encoded_keyword = quote(keyword)
            headers = {'Referer': f'https://so.360kan.com/?kw={encoded_keyword}'}
            response = await self.client.get(search_url, params=params, headers=headers)
            response.raise_for_status()

            json_text = response.text
            # 修正：采用参考脚本中更健壮的JSONP解析方式
            try:
                start_index = json_text.index('(') + 1
                end_index = json_text.rindex(')')
                json_payload = json_text[start_index:end_index]
            except ValueError:
                json_payload = json_text # 如果找不到括号，则假定它不是JSONP
            data = So360SearchResponse.model_validate(json.loads(json_payload))
            
            if not data.data or not data.data.longData:
                return []

            results: List[models.MetadataDetailsResponse] = []
            for item in data.data.longData.rows:
                media_type = "other"
                if item.cat_name:
                    if "电影" in item.cat_name: media_type = "movie"
                    elif "动漫" in item.cat_name or "电视" in item.cat_name: media_type = "tv_series"

                media_id = item.en_id or item.id

                results.append(models.MetadataDetailsResponse(
                    id=media_id,
                    provider=self.provider_name,
                    title=item.title,
                    type=media_type,
                    imageUrl=item.cover,
                    year=int(item.year) if item.year and item.year.isdigit() else None,
                    aliasesCn=item.alias or [],
                    extra={"playlinks": item.playlinks, "item_data": item.model_dump(by_alias=True)}
                ))

            return results
        except httpx.ConnectError as e:
            self.logger.error(f"360影视搜索失败 for '{keyword}': 无法连接到服务器。请检查网络或代理设置。 {e}")
            return []
        except json.JSONDecodeError:
            self.logger.error(f"360影视搜索失败 for '{keyword}': 响应不是有效的JSON。响应内容: {response.text[:200]}...")
            return []
        except Exception as e:
            self.logger.error(f"360影视搜索失败 for '{keyword}': {e}", exc_info=True)
            return []

    async def find_url_for_provider(self, keyword: str, target_provider: str, user: models.User, season: Optional[int] = None) -> Optional[str]:
        """通过360搜索查找指定平台（如腾讯、B站）的播放链接。"""
        provider_map = { "tencent": "qq", "iqiyi": "qiyi", "youku": "youku", "bilibili": "bilibili", "mgtv": "imgo" }
        target_site = provider_map.get(target_provider)
        if not target_site:
            self.logger.debug(f"360故障转移：不支持的目标平台 '{target_provider}'")
            return None

        search_results = await self.search(keyword, user)
        if not search_results: return None

        best_match = None
        if season is not None:
            candidates = [r for r in search_results if get_season_from_title(r.title) == season]
            if candidates: best_match = candidates[0]
        else:
            best_match = search_results[0]
        
        if not best_match:
            self.logger.info(f"360故障转移：未找到与 '{keyword}' S{season} 匹配的结果。")
            return None
        
        playlinks = best_match.extra.get("playlinks", {}) if best_match.extra else {}
        link_info = playlinks.get(target_site)

        if isinstance(link_info, str):
            return link_info
        elif isinstance(link_info, list) and link_info:
            # 对于综艺节目，其播放链接是一个列表，我们返回第一集的URL
            first_episode = link_info[0]
            if isinstance(first_episode, dict) and 'url' in first_episode:
                return first_episode['url']
        return None

    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        possible_paths = [f"/dianshiju/{item_id}.html", f"/dongman/{item_id}.html", f"/dianying/{item_id}.html"]
        try:
            for path in possible_paths:
                detail_url = f"{self.web_base_url}{path}"
                try:
                    response = await self.client.get(detail_url)
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, "lxml")
                        script_tag = soup.find("script", string=re.compile(r"window\.g_initialData\s*="))
                        if not script_tag: continue

                        json_str_match = re.search(r"window\.g_initialData\s*=\s*({.*?});", script_tag.string)
                        if not json_str_match: continue
                            
                        initial_data = json.loads(json_str_match.group(1))
                        cover_info_raw = initial_data.get("coverInfo", {}).get("coverInfo")
                        if not cover_info_raw: continue
                            
                        cover_info = So360CoverInfo.model_validate(cover_info_raw)
                        aliases = [cover_info.sub_title] if cover_info.sub_title else []

                        return models.MetadataDetailsResponse(
                            id=item_id,
                            provider=self.provider_name,
                            title=cover_info.title,
                            type=cover_info.media_type,
                            imageUrl=cover_info.cover,
                            details=cover_info.description,
                            aliasesCn=aliases,
                            year=int(cover_info.year) if cover_info.year and cover_info.year.isdigit() else None
                        )
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        continue
                    raise
            
            self.logger.warning(f"无法通过任何已知路径找到 {item_id} 的详情页。")
            return None
        except Exception as e:
            self.logger.error(f"获取360影视详情失败 (ID: {item_id}): {e}", exc_info=True)
            return None

    async def get_comments_by_failover(self, title: str, season: int, episode_index: int, user: models.User) -> Optional[List[dict]]:
        self.logger.info(f"360 Failover: Searching for '{title}' S{season}E{episode_index}")
        search_results = await self.search(keyword=title, user=user)
        if not search_results:
            self.logger.info("360 Failover: Initial search returned no results.")
            return None

        best_match = next((r for r in search_results if get_season_from_title(r.title) == season), search_results[0])
        self.logger.info(f"360 Failover: Found best match: '{best_match.title}' (ID: {best_match.id})")

        episode_url = await self._get_episode_url_from_360(best_match, episode_index)
        if not episode_url:
            self.logger.info(f"360 Failover: Could not find a URL for episode {episode_index}.")
            return None
        
        self.logger.info(f"360 Failover: Found episode URL: {episode_url}")

        try:
            scraper = self.scraper_manager.get_scraper_by_domain(episode_url)
            if not scraper:
                self.logger.warning(f"360 Failover: No scraper available for domain of URL: {episode_url}")
                return None
            
            provider_episode_id = await scraper.get_id_from_url(episode_url)
            if not provider_episode_id:
                self.logger.warning(f"360 Failover: Could not extract ID from URL: {episode_url}")
                return None
            
            episode_id_for_comments = scraper.format_episode_id_for_comments(provider_episode_id)
            self.logger.info(f"360 Failover: Getting comments from provider '{scraper.provider_name}' with ID '{episode_id_for_comments}'")
            
            comments = await scraper.get_comments(episode_id_for_comments)
            return comments
        except Exception as e:
            self.logger.error(f"360 Failover: Error getting comments from URL '{episode_url}': {e}", exc_info=True)
            return None

    def _convert_hunantv_to_mgtv(self, url: str) -> str:
        m = re.match(r'https?://www\.hunantv\.com/v/1/(\d+)/f/(\d+)\.html', url)
        if m:
            new_url = f'https://www.mgtv.com/b/{m.group(1)}/{m.group(2)}.html'
            self.logger.debug(f"Converted hunantv URL '{url}' to '{new_url}'")
            return new_url
        return url

    async def _get_episode_url_from_360(self, best_match: models.MetadataDetailsResponse, episode_index: int) -> Optional[str]:
        try:
            item_data_dict = best_match.extra.get("item_data", {}) if best_match.extra else {}
            item_data = So360SearchResultItem.model_validate(item_data_dict)

            cat_id = item_data.cat_id
            ent_id = item_data.id if (cat_id == '3' or ('综艺' in (item_data.cat_name or ''))) else item_data.en_id
            if not ent_id: ent_id = item_data.id
            
            cat_name = item_data.cat_name
            year = item_data.year
            playlinks = item_data.playlinks

            platform_order = ['qq', 'qiyi', 'youku', 'bilibili', 'bilibili1', 'imgo']
 
            for site in platform_order:
                if site in playlinks:
                    self.logger.info(f"360 Failover: Checking platform '{site}' for episodes.")
                    episodes = await self._get_360_platform_episodes(cat_id, ent_id, site, cat_name, year, item_data)
                    if episodes and len(episodes) >= episode_index:
                        episode_data = episodes[episode_index - 1]
                        url = episode_data.get('url') if isinstance(episode_data, dict) else episode_data if isinstance(episode_data, str) else None
                        if url:
                            return self._convert_hunantv_to_mgtv(url)
            return None
        except Exception as e:
            self.logger.error(f"360 Failover: _get_episode_url_from_360 failed: {e}", exc_info=True)
            return None

    async def _get_360_platform_episodes(self, cat_id: Optional[str], ent_id: Optional[str], site: str, cat_name: Optional[str], year: Optional[str], item: So360SearchResultItem) -> List[Any]:
        if (cat_id == '3' or (cat_name and '综艺' in cat_name)):
            years_to_check = []
            if item.playlinks_year and site in item.playlinks_year:
                years_to_check = [str(y) for y in item.playlinks_year[site] if y]
            if not years_to_check and item.years:
                years_to_check = [str(y) for y in item.years if y]
            if not years_to_check and year:
                years_to_check = [year]

            all_episodes = []
            for y in years_to_check:
                offset = 0
                while True:
                    params = {'site': site, 'y': y, 'entid': ent_id, 'offset': offset, 'count': 100, 'v_ap': '1', 'cb': '__jp7'}
                    try:
                        resp = await self.client.get(f'{self.api_base_url}/episodeszongyi', params=params)
                        json_text = resp.text.strip()
                        if json_text.startswith('__jp7('): json_text = json_text[len('__jp7('):-1]
                        parsed = json.loads(json_text)
                        if parsed.get('code') == 0 and parsed.get('data'):
                            episodes = parsed['data'].get('list', [])
                            all_episodes.extend(episodes)
                            if len(episodes) < 100: break
                            offset += 100
                        else: break
                    except Exception as e:
                        self.logger.error(f"360 Failover: 获取综艺分集失败 (year: {y}): {e}")
                        break
            return all_episodes
        else:
            s_param = json.dumps([{"cat_id": cat_id, "ent_id": ent_id, "site": site}])
            params = {'v_ap': '1', 's': s_param, 'cb': '__jp8'}
            try:
                resp = await self.client.get(f'{self.api_base_url}/episodesv2', params=params)
                json_text = resp.text.strip()
                if json_text.startswith('__jp8('):
                    json_text = json_text[len('__jp8('):-1]
                parsed = json.loads(json_text)
                if parsed.get('code') == 0 and parsed.get('data'):
                    series_html = parsed['data'][0].get('seriesHTML', {})
                    if 'seriesPlaylinks' in series_html:
                        return series_html['seriesPlaylinks']
            except Exception as e:
                self.logger.error(f"360 Failover: 获取剧集分集失败: {e}")
        return []

    async def search_aliases(self, keyword: str, user: models.User) -> Set[str]:
        search_results = await self.search(keyword, user)
        aliases: Set[str] = set()
        for item in search_results:
            aliases.add(item.title)
            if item.aliasesCn:
                aliases.update(item.aliasesCn)
        return {alias for alias in aliases if alias}

    async def check_connectivity(self) -> str:
        try:
            # 修正：连接检测应模拟真实搜索流程
            response = await self.client.get(f"{self.api_base_url}/index?kw=test&cb=__jp0", timeout=10.0)
            response.raise_for_status()
            text = response.text
            # 使用与搜索相同的健壮解析逻辑
            start_index = text.index('(') + 1
            end_index = text.rindex(')')
            json_payload = text[start_index:end_index]
            # 尝试解析，如果成功则认为连接正常
            json.loads(json_payload)
            return "连接成功"
        except json.JSONDecodeError:
            return "连接失败 (API响应不是有效的JSON)"
        except ValueError: # .index or .rindex fails
            return "连接失败 (API响应格式不正确)"
        except Exception as e:
            self.logger.error(f"360影视连接检查失败: {e}")
            return "连接失败"
            
    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: models.User, request: Any) -> Any:
        raise NotImplementedError(f"操作 '{action_name}' 在 {self.provider_name} 中未实现。")

    async def close(self):
        await self.client.aclose()
