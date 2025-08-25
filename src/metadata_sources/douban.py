import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Set

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError

from .. import crud, models
from .base import BaseMetadataSource, HTTPStatusError

logger = logging.getLogger(__name__)

# --- Pydantic Models for Douban JSON API ---
class DoubanJsonSearchSubject(BaseModel):
    id: str
    title: str
    url: str
    cover: str
    rate: str
    cover_x: int
    cover_y: int

class DoubanJsonSearchResponse(BaseModel):
    subjects: List[DoubanJsonSearchSubject]

# --- Main Metadata Source Class ---
class DoubanMetadataSource(BaseMetadataSource): # type: ignore
    provider_name = "douban" # type: ignore

    async def _create_client(self) -> httpx.AsyncClient:
        """Creates an httpx.AsyncClient with Douban cookie and proxy settings."""
        cookie = await self.config_manager.get("doubanCookie", "")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if cookie:
            headers["Cookie"] = cookie

        proxy_url = await self.config_manager.get("proxy_url", "")
        proxy_enabled_globally = (await self.config_manager.get("proxy_enabled", "false")).lower() == 'true'

        async with self._session_factory() as session:
            metadata_settings = await crud.get_all_metadata_source_settings(session)

        provider_setting = next((s for s in metadata_settings if s['providerName'] == self.provider_name), None)
        use_proxy_for_this_provider = provider_setting.get('use_proxy', False) if provider_setting else False

        proxy_to_use = proxy_url if proxy_enabled_globally and use_proxy_for_this_provider and proxy_url else None

        return httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True, proxy=proxy_to_use)

    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        self.logger.info(f"豆瓣: 正在使用JSON API搜索 '{keyword}'")
        try:
            async with await self._create_client() as client:
                movie_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "movie", "tag": keyword, "page_limit": 20, "page_start": 0})
                tv_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "tv", "tag": keyword, "page_limit": 20, "page_start": 0})
                movie_res, tv_res = await asyncio.gather(movie_task, tv_task, return_exceptions=True)

                all_subjects = []
                for res in [movie_res, tv_res]:
                    if isinstance(res, httpx.Response) and res.status_code == 200:
                        try:
                            data = DoubanJsonSearchResponse.model_validate(res.json())
                            all_subjects.extend(data.subjects)
                        except ValidationError as e:
                            self.logger.warning(f"解析豆瓣JSON API响应失败: {e} - 响应: {res.text[:200]}")
                    elif isinstance(res, Exception):
                        self.logger.error(f"请求豆瓣JSON API时发生网络错误: {res}")

                seen_ids = set()
                results = []
                for subject in all_subjects:
                    if subject.id not in seen_ids:
                        results.append(models.MetadataDetailsResponse(
                            id=subject.id, doubanId=subject.id, title=subject.title,
                            details=f"评分: {subject.rate}", imageUrl=subject.cover,
                        ))
                        seen_ids.add(subject.id)
                return results
        except Exception as e:
            self.logger.error(f"豆瓣搜索失败，发生意外错误: {e}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="豆瓣搜索时发生内部错误。")

    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        self.logger.info(f"豆瓣: 正在获取详情 item_id={item_id}")
        try:
            async with await self._create_client() as client:
                details_url = f"https://movie.douban.com/subject/{item_id}/"
                response = await client.get(details_url)
                response.raise_for_status()
                html = response.text

                title_match = re.search(r'<span property="v:itemreviewed">(.*?)</span>', html)
                title = title_match.group(1).strip() if title_match else ""

                aliases_cn = []
                alias_match = re.search(r'<span class="pl">又名:</span>(.*?)<br/>', html)
                if alias_match:
                    aliases_text = alias_match.group(1)
                    aliases_cn = [alias.strip() for alias in aliases_text.split("/") if alias.strip()]

                imdb_id_match = re.search(r'<a href="https://www.imdb.com/title/(tt\d+)"', html)
                imdb_id = imdb_id_match.group(1) if imdb_id_match else None

                if title:
                    aliases_cn.insert(0, title)
                aliases_cn = list(dict.fromkeys(aliases_cn))

                return models.MetadataDetailsResponse(
                    id=item_id, doubanId=item_id, title=title,
                    imdbId=imdb_id, aliasesCn=aliases_cn,
                )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                self.logger.error(f"豆瓣详情页请求被拒绝(403)，可能是Cookie已失效或IP被限制。ID: {item_id}")
                raise HTTPException(status_code=403, detail="豆瓣请求被拒绝，请检查Cookie或网络环境。")
            raise HTTPException(status_code=500, detail=f"请求豆瓣详情时发生错误: {e}")
        except Exception as e:
            self.logger.error(f"解析豆瓣详情页时发生错误: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="解析豆瓣详情页失败。")

    async def search_aliases(self, keyword: str, user: models.User) -> Set[str]:
        self.logger.info(f"豆瓣: 正在为 '{keyword}' 搜索别名")
        local_aliases: Set[str] = set()
        try:
            async with await self._create_client() as client:
                movie_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "movie", "tag": keyword, "page_limit": 1, "page_start": 0})
                tv_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "tv", "tag": keyword, "page_limit": 1, "page_start": 0})
                movie_res, tv_res = await asyncio.gather(movie_task, tv_task, return_exceptions=True)

                best_subject_id = None
                if isinstance(movie_res, httpx.Response) and movie_res.status_code == 200:
                    if subjects := movie_res.json().get('subjects', []):
                        best_subject_id = subjects[0]['id']
                if not best_subject_id and isinstance(tv_res, httpx.Response) and tv_res.status_code == 200:
                    if subjects := tv_res.json().get('subjects', []):
                        best_subject_id = subjects[0]['id']

                if best_subject_id:
                    details = await self.get_details(best_subject_id, user)
                    if details and details.aliasesCn:
                        local_aliases.update(details.aliasesCn)
                
                self.logger.info(f"豆瓣辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
        except Exception as e:
            self.logger.warning(f"豆瓣辅助搜索失败: {e}")
        return {alias for alias in local_aliases if alias}

    async def check_connectivity(self) -> str:
        try:
            async with await self._create_client() as client:
                response = await client.get("https://movie.douban.com", timeout=10.0)
                if "sec.douban.com" in str(response.url):
                    return "连接失败 (被重定向到验证页面，请检查Cookie)"
                return "连接成功" if response.status_code == 200 else f"连接失败 (状态码: {response.status_code})"
        except Exception as e:
            return f"连接失败: {e}"

    async def execute_action(self, action_name: str, payload: Dict, user: models.User) -> Any:
        """Douban source does not support custom actions."""
        raise NotImplementedError(f"源 '{self.provider_name}' 不支持任何自定义操作。")