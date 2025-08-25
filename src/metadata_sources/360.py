import asyncio
import json
import logging
import urllib.parse
import re
from typing import Any, Dict, List, Optional, Set

import httpx
from bs4 import BeautifulSoup # type: ignore
from pydantic import BaseModel, Field, ValidationError

from .. import models
from .base import BaseMetadataSource

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
    playlinks: Dict[str, str] = Field(default_factory=dict)

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
        # "动漫" or "电视剧" are both treated as tv_series for simplicity
        return "tv_series"

# --- Main Scraper Class ---

class So360MetadataSource(BaseMetadataSource):
    provider_name = "360"

    def __init__(self, session_factory, config_manager, scraper_manager):
        super().__init__(session_factory, config_manager, scraper_manager)
        self.api_base_url = "https://api.so.360.cn"
        self.web_base_url = "https://www.360kan.com"
        self.client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
            timeout=20.0,
            follow_redirects=True
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
            headers = {'referer': f'https://so.360kan.com/?kw={urllib.parse.quote(keyword)}'}
            response = await self.client.get(search_url, params=params, headers=headers)
            response.raise_for_status()
            
            json_text = response.text
            if json_text.startswith('__jp0('):
                json_text = json_text[len('__jp0('):-1]
            
            data = So360SearchResponse.model_validate(json.loads(json_text))
            
            if not data.data or not data.data.longData:
                return []

            results = []
            for item in data.data.longData.rows:
                media_type = "other"
                if item.cat_name:
                    if "电影" in item.cat_name: media_type = "movie"
                    elif "动漫" in item.cat_name or "电视" in item.cat_name: media_type = "tv_series"

                # 使用 en_id 作为更稳定的ID，如果不存在则回退到 id
                media_id = item.en_id or item.id

                results.append(models.MetadataDetailsResponse(
                    id=media_id,
                    title=item.title,
                    type=media_type,
                    imageUrl=item.cover,
                    year=int(item.year) if item.year and item.year.isdigit() else None
                ))

            return results

        except Exception as e:
            self.logger.error(f"360影视搜索失败 for '{keyword}': {e}", exc_info=True)
            return []

    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        try:
            # 360源的item_id现在是en_id或id，我们需要通过它找到详情页URL，这通常需要一次搜索
            search_results = await self.search(keyword=item_id, user=user)
            if not search_results: return None
            return search_results[0]
        except ValueError:
            self.logger.error(f"无效的360影视ID格式: {item_id}")
            response = await self.client.get(detail_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "lxml")
            script_tag = soup.find("script", string=re.compile(r"window\.g_initialData"))
            if not script_tag:
                self.logger.warning(f"在页面 {detail_url} 未找到 g_initialData")
                return None

            json_str_match = re.search(r"window\.g_initialData\s*=\s*({.*?});", script_tag.string)
            if not json_str_match:
                self.logger.warning(f"在页面 {detail_url} 未能从script中提取JSON")
                return None
                
            initial_data = json.loads(json_str_match.group(1))
            cover_info_raw = initial_data.get("coverInfo", {}).get("coverInfo")
            if not cover_info_raw:
                self.logger.warning(f"在页面 {detail_url} 的JSON中未找到 coverInfo")
                return None
                
            cover_info = So360CoverInfo.model_validate(cover_info_raw)
            
            aliases = [cover_info.sub_title] if cover_info.sub_title else []

            return models.MetadataDetailsResponse(
                id=item_id,
                title=cover_info.title,
                type=cover_info.media_type,
                imageUrl=cover_info.cover,
                details=cover_info.description,
                aliasesCn=aliases,
                year=int(cover_info.year) if cover_info.year and cover_info.year.isdigit() else None
            )
        except Exception as e:
            self.logger.error(f"获取360影视详情失败 (URL: {detail_url}): {e}", exc_info=True)
            return None

    async def get_comments_by_failover(self, title: str, season: int, episode_index: int, user: models.User) -> Optional[List[dict]]:
        self.logger.info(f"360 Failover: Searching for '{title}' S{season}E{episode_index}")
        
        # 1. Search 360 for the anime
        search_results = await self.search(keyword=title, user=user)
        if not search_results:
            self.logger.info("360 Failover: Initial search returned no results.")
            return None

        # 2. Find the best match
        best_match = next((r for r in search_results if r.season == season), search_results[0])
        self.logger.info(f"360 Failover: Found best match: '{best_match.title}' (ID: {best_match.id})") # 这里用的是 ProviderSearchInfo 的 id

        # 3. Get episode URL from 360's other platform links
        episode_url = await self._get_episode_url_from_360(best_match.title, episode_index) # 使用标题进行搜索
        if not episode_url:
            self.logger.info(f"360 Failover: Could not find a URL for episode {episode_index}.")
            return None
        
        self.logger.info(f"360 Failover: Found episode URL: {episode_url}")

        # 4. Use ScraperManager to get comments from the URL
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
        """将hunantv.com的URL转换为mgtv.com的URL。"""
        m = re.match(r'https?://www\.hunantv\.com/v/1/(\d+)/f/(\d+)\.html', url)
        if m:
            new_url = f'https://www.mgtv.com/b/{m.group(1)}/{m.group(2)}.html'
            self.logger.debug(f"Converted hunantv URL '{url}' to '{new_url}'")
            return new_url
        return url

    async def _get_episode_url_from_360(self, media_id: str, episode_index: int) -> Optional[str]:
        try:
            # 搜索接口返回的 media_id 实际上是标题，我们需要用它重新搜索以获取 ent_id
            params = {'kw': media_id, 'from': '', 'force_v': '1', 'v_ap': '1', 'tab': 'all', 'cb': '__jp0'}
            headers = {'referer': f'https://so.360kan.com/?kw={urllib.parse.quote(media_id)}'}
            response = await self.client.get(f"{self.api_base_url}/index", params=params, headers=headers)
            response.raise_for_status()
            json_text = response.text
            if json_text.startswith('__jp0('):
                json_text = json_text[len('__jp0('):-1]
            
            data = json.loads(json_text)
            rows = data.get('data', {}).get('longData', {}).get('rows', [])
            if not rows: return None
            item_data = rows[0]

            cat_id = item_data.get('cat_id')
            # 参考脚本逻辑：综艺用id，其他用en_id
            ent_id = item_data.get('id') if (cat_id == '3' or ('综艺' in item_data.get('cat_name', ''))) else item_data.get('en_id')
            if not ent_id:
                ent_id = item_data.get('id') # Fallback to id if en_id is missing
 
            cat_name = item_data.get('cat_name')
            year = item_data.get('year')
            playlinks = item_data.get('playlinks', {})

            platform_order = ['qq', 'qiyi', 'youku', 'bilibili', 'bilibili1', 'imgo']
 
            for site in platform_order:
                if site in playlinks:
                    self.logger.info(f"360 Failover: Checking platform '{site}' for episodes.")
                    episodes = await self._get_360_platform_episodes(cat_id, ent_id, site, cat_name, year, item_data)
                    if episodes and len(episodes) >= episode_index:
                        episode_data = episodes[episode_index - 1]
                        url = episode_data.get('url') if isinstance(episode_data, dict) else episode_data if isinstance(episode_data, str) else None
                        if url:
                            # 新增：转换湖南TV的URL
                            return self._convert_hunantv_to_mgtv(url)
            return None
        except Exception as e:
            self.logger.error(f"360 Failover: _get_episode_url_from_360 failed: {e}", exc_info=True)
            return None

    async def _get_360_platform_episodes(self, cat_id, ent_id, site, cat_name, year, item) -> List[Any]:
        # 综艺类型，走分页接口 (当前实现只取第一页)
        if (cat_id == '3' or (cat_name and '综艺' in cat_name)):
            params = {'site': site, 'y': year or '', 'entid': ent_id, 'offset': 0, 'count': 100, 'v_ap': '1', 'cb': '__jp7'}
            try:
                resp = await self.client.get(f'{self.api_base_url}/episodeszongyi', params=params)
                json_text = resp.text
                if json_text.startswith('__jp7('):
                    json_text = json_text[len('__jp7('):-1]
                parsed = json.loads(json_text)
                if parsed.get('code') == 0 and parsed.get('data'):
                    return parsed['data'].get('list', [])
            except Exception as e:
                self.logger.error(f"360 Failover: 获取综艺分集失败: {e}")
            return []
        else:
            # 其他类型，走 episodesv2 接口
            s_param = json.dumps([{"cat_id": cat_id, "ent_id": ent_id, "site": site}])
            params = {'v_ap': '1', 's': s_param, 'cb': '__jp8'}
            try:
                resp = await self.client.get(f'{self.api_base_url}/episodesv2', params=params)
                json_text = resp.text
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
        if search_results:
            best_match = search_results[0]
            aliases.add(best_match.title)
            if best_match.aliasesCn:
                aliases.update(best_match.aliasesCn)
        return {alias for alias in aliases if alias}

    async def check_connectivity(self) -> str:
        try:
            response = await self.client.get(self.web_base_url, timeout=10.0)
            if response.status_code == 200:
                return "连接成功"
            return f"连接失败 (状态码: {response.status_code})"
        except Exception as e:
            self.logger.error(f"360影视连接检查失败: {e}")
            return "连接失败"
            
    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: models.User, request) -> Any:
        return await super().execute_action(action_name, payload, user, request)

    async def close(self):
        await self.client.aclose()
