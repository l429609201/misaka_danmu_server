import asyncio
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError, field_validator

from src.db import crud, models
from .base import BaseMetadataSource, HTTPStatusError

logger = logging.getLogger(__name__)
# 新增：获取用于记录元数据响应的专用 logger
metadata_logger = logging.getLogger('metadata_responses')

# --- Pydantic Models for Douban JSON API ---
class DoubanJsonSearchSubject(BaseModel):
    id: str
    title: str
    url: str
    cover: str
    rate: str
    cover_x: Optional[int] = None
    cover_y: Optional[int] = None

    @field_validator('cover_x', 'cover_y', mode='before')
    @classmethod
    def convert_empty_to_none(cls, v):
        """将空字符串转换为None"""
        if v == '' or v is None:
            return None
        return v

class DoubanJsonSearchResponse(BaseModel):
    subjects: List[DoubanJsonSearchSubject]

# --- Main Metadata Source Class ---
class DoubanMetadataSource(BaseMetadataSource): # type: ignore
    provider_name = "douban" # type: ignore
    test_url = "https://movie.douban.com"
    has_force_aux_search_toggle = True # 新增：硬编码标志
    is_failover_source = True
    supports_episode_urls = True  # 支持获取分集URL
    async def _create_client(self) -> httpx.AsyncClient:
        """Creates an httpx.AsyncClient with Douban cookie and proxy settings."""
        cookie = await self.config_manager.get("doubanCookie", "")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if cookie:
            headers["Cookie"] = cookie

        # 获取代理模式
        proxy_mode = await self.config_manager.get("proxyMode", "none")

        # 兼容旧配置：如果 proxyMode 为 none 但 proxyEnabled 为 true，则使用 http_socks 模式
        if proxy_mode == "none":
            proxy_enabled_globally = (await self.config_manager.get("proxyEnabled", "false")).lower() == 'true'
            if proxy_enabled_globally:
                proxy_mode = "http_socks"

        proxy_to_use = None

        # 只有 http_socks 模式才需要设置 httpx 的 proxy 参数
        if proxy_mode == "http_socks":
            proxy_url = await self.config_manager.get("proxyUrl", "")
            if proxy_url:
                async with self._session_factory() as session:
                    metadata_settings = await crud.get_all_metadata_source_settings(session)

                provider_setting = next((s for s in metadata_settings if s['providerName'] == self.provider_name), None)
                use_proxy_for_this_provider = provider_setting.get('useProxy', False) if provider_setting else False

                proxy_to_use = proxy_url if use_proxy_for_this_provider else None

        return httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True, proxy=proxy_to_use)

    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        self.logger.info(f"豆瓣: 正在使用JSON API搜索 '{keyword}'")
        try:
            provider_setting = await self._get_provider_setting()
            log_raw = provider_setting.get('logRawResponses', False)

            async def log_response(name: str, response: httpx.Response):
                if log_raw:
                    log_message = (
                        f"Douban API Response for '{name}':\n"
                        f"URL: {response.url}\n"
                        f"Status Code: {response.status_code}\n"
                        f"Body: {response.text}\n"
                        "----------------------------------------"
                    )
                    metadata_logger.info(log_message)

            async with await self._create_client() as client:
                movie_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "movie", "tag": keyword, "page_limit": 20, "page_start": 0})
                tv_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "tv", "tag": keyword, "page_limit": 20, "page_start": 0})
                movie_res, tv_res = await asyncio.gather(movie_task, tv_task, return_exceptions=True)

                all_subjects = []
                # 记录每个subject的类型
                subject_types = {}

                # 处理电影结果
                if isinstance(movie_res, httpx.Response) and movie_res.status_code == 200:
                    await log_response(f"search '{keyword}'", movie_res)
                    try:
                        data = DoubanJsonSearchResponse.model_validate(movie_res.json())
                        all_subjects.extend(data.subjects)
                        for subject in data.subjects:
                            subject_types[subject.id] = "movie"
                    except ValidationError as e:
                        self.logger.warning(f"解析豆瓣JSON API响应失败: {e} - 响应: {movie_res.text[:200]}")
                elif isinstance(movie_res, Exception):
                    self.logger.error(f"请求豆瓣JSON API时发生网络错误: {movie_res}")

                # 处理电视剧结果
                if isinstance(tv_res, httpx.Response) and tv_res.status_code == 200:
                    await log_response(f"search '{keyword}'", tv_res)
                    try:
                        data = DoubanJsonSearchResponse.model_validate(tv_res.json())
                        all_subjects.extend(data.subjects)
                        for subject in data.subjects:
                            subject_types[subject.id] = "tv_series"
                    except ValidationError as e:
                        self.logger.warning(f"解析豆瓣JSON API响应失败: {e} - 响应: {tv_res.text[:200]}")
                elif isinstance(tv_res, Exception):
                    self.logger.error(f"请求豆瓣JSON API时发生网络错误: {tv_res}")

                seen_ids = set()
                results = []
                for subject in all_subjects:
                    if subject.id not in seen_ids:
                        results.append(models.MetadataDetailsResponse(
                            id=subject.id, doubanId=subject.id, title=subject.title,
                            details=f"评分: {subject.rate}", imageUrl=subject.cover,
                            type=subject_types.get(subject.id, "unknown"),  # 从记录的类型中获取
                            supportsEpisodeUrls=False  # 豆瓣源不支持获取分集URL (Frodo API的vendors数组为空)
                        ))
                        seen_ids.add(subject.id)
                return results
        except Exception as e:
            self.logger.error(f"豆瓣搜索失败，发生意外错误: {e}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="豆瓣搜索时发生内部错误。")

    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        self.logger.info(f"豆瓣: 正在获取详情 item_id={item_id}")
        try:
            provider_setting = await self._get_provider_setting()
            log_raw = provider_setting.get('logRawResponses', False)

            async with await self._create_client() as client:
                details_url = f"https://movie.douban.com/subject/{item_id}/"
                response = await client.get(details_url)

                if log_raw:
                    log_message = f"Douban Detail Page Response for ID '{item_id}':\nStatus: {response.status_code}\nBody:\n{response.text[:1000]}...\n----------------------------------------"
                    metadata_logger.info(log_message)

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

                # 提取年份信息
                year = None
                year_match = re.search(r'<span class="year">\((\d{4})\)</span>', html)
                if year_match:
                    try:
                        year = int(year_match.group(1))
                    except (ValueError, TypeError):
                        pass

                if title:
                    aliases_cn.insert(0, title)
                aliases_cn = list(dict.fromkeys(aliases_cn))

                return models.MetadataDetailsResponse(
                    id=item_id, doubanId=item_id, title=title,
                    imdbId=imdb_id, aliasesCn=aliases_cn, year=year,
                    supportsEpisodeUrls=False  # 豆瓣源不支持获取分集URL (Frodo API的vendors数组为空)
                )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                self.logger.error(f"豆瓣详情页请求被拒绝(403)，可能是Cookie已失效或IP被限制。ID: {item_id}")
                raise HTTPException(status_code=403, detail="豆瓣请求被拒绝，请检查Cookie或网络环境。")
            raise HTTPException(status_code=500, detail=f"请求豆瓣详情时发生错误: {e}")
        except Exception as e:
            self.logger.error(f"解析豆瓣详情页时发生错误: {e}")
            raise HTTPException(status_code=500, detail="解析豆瓣详情页失败。")

    async def search_aliases(self, keyword: str, user: models.User) -> Set[str]:
        self.logger.info(f"豆瓣: 正在为 '{keyword}' 搜索别名")
        local_aliases: Set[str] = set()
        try:
            provider_setting = await self._get_provider_setting()
            log_raw = provider_setting.get('logRawResponses', False)

            async with await self._create_client() as client:
                movie_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "movie", "tag": keyword, "page_limit": 1, "page_start": 0})
                tv_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "tv", "tag": keyword, "page_limit": 1, "page_start": 0})
                movie_res, tv_res = await asyncio.gather(movie_task, tv_task, return_exceptions=True)

                best_subject_id = None
                if isinstance(movie_res, httpx.Response) and movie_res.status_code == 200:
                    if log_raw:
                        metadata_logger.info(
                            f"Douban Alias Search (Movie) for '{keyword}':\nBody: {movie_res.text}\n----------------------------------------"
                        )
                    if subjects := movie_res.json().get('subjects', []):
                        best_subject_id = subjects[0]['id']
                if not best_subject_id and isinstance(tv_res, httpx.Response) and tv_res.status_code == 200:
                    if log_raw:
                        metadata_logger.info(
                            f"Douban Alias Search (TV) for '{keyword}':\nBody: {tv_res.text}\n----------------------------------------"
                        )
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

    async def check_connectivity(self) -> Dict[str, str]:
        """检查豆瓣源配置状态"""
        try:
            # 豆瓣Cookie是可选的，没有Cookie也可以正常使用（匿名访问）
            # 只有被流控或封禁时才需要填Cookie
            douban_cookie = await self.config_manager.get("doubanCookie", "")
            if not douban_cookie or douban_cookie.strip() == "":
                return {"code": "ok", "message": "配置正常 (匿名访问，被流控时可配置Cookie)"}

            # 填了Cookie，检查格式是否合理
            if "bid=" not in douban_cookie and "dbcl2=" not in douban_cookie:
                return {"code": "warning", "message": "Cookie已配置但格式可能不正确 (建议检查)"}

            return {"code": "ok", "message": "配置正常 (已配置Cookie)"}
        except Exception as e:
            return {"code": "error", "message": f"配置检查失败: {e}"}

    async def execute_action(self, action_name: str, payload: Dict, user: models.User) -> Any:
        """Douban source does not support custom actions."""
        raise NotImplementedError(f"源 '{self.provider_name}' 不支持任何自定义操作。")

    async def _get_provider_setting(self) -> Dict[str, Any]:
        """辅助函数,用于从数据库获取当前源的设置。"""
        async with self._session_factory() as session:
            settings = await crud.get_all_metadata_source_settings(session)
            provider_setting = next((s for s in settings if s['providerName'] == self.provider_name), None)
            return provider_setting or {}

    async def get_episode_urls(self, metadata_id: str, target_provider: Optional[str] = None) -> List[Tuple[int, str]]:
        """
        获取分集URL列表 (补充源功能)。

        Args:
            metadata_id: 豆瓣条目ID
            target_provider: 目标平台 (tencent/iqiyi/youku/bilibili/mgtv), 如果为None则返回所有平台

        Returns:
            List[Tuple[int, str]]: (集数, 播放URL) 的列表
        """
        return await self._get_episode_urls_from_douban_page(metadata_id, target_provider)

    def _generate_douban_signature(self, url_path: str, timestamp: str, method: str = 'GET') -> str:
        """
        生成豆瓣Frodo API签名

        Args:
            url_path: URL路径 (例如: /api/v2/movie/1419297)
            timestamp: 时间戳 (格式: YYYYMMDD)
            method: HTTP方法 (默认: GET)

        Returns:
            Base64编码的HMAC-SHA1签名
        """
        import hmac
        import hashlib
        import base64
        from urllib.parse import quote

        secret_key = "bf7dddc7c9cfe6f7"

        # 构造签名原文: METHOD&URL_PATH&TIMESTAMP
        raw_sign = '&'.join([
            method.upper(),
            quote(url_path, safe=''),
            str(timestamp)
        ])

        # HMAC-SHA1签名
        signature = hmac.new(
            secret_key.encode(),
            raw_sign.encode(),
            hashlib.sha1
        ).digest()

        # Base64编码
        return base64.b64encode(signature).decode()

    async def _get_episode_urls_from_douban_page(self, douban_id: str, target_provider: Optional[str] = None) -> List[Tuple[int, str]]:
        """
        从豆瓣Frodo API获取播放链接

        Args:
            douban_id: 豆瓣条目ID
            target_provider: 目标平台 (tencent/iqiyi/youku/bilibili/mgtv), 如果为None则返回所有平台

        Returns:
            List[Tuple[int, str]]: (集数, 播放URL) 的列表
        """
        from datetime import datetime

        # 获取配置
        provider_setting = await self._get_provider_setting()
        log_raw = provider_setting.get('logRawResponses', False)

        # 平台ID映射
        provider_to_vendor_id = {
            "tencent": "qq",      # 腾讯视频
            "iqiyi": "iqiyi",     # 爱奇艺
            "youku": "youku",     # 优酷
            "bilibili": "bilibili", # B站
            "mgtv": "mgtv"        # 芒果TV
        }

        # 使用豆瓣Frodo API (参考MoviePilot的实现)
        api_key = "0dad551ec0f84ed02907ff5c42e8ec70"
        base_url = "https://frodo.douban.com/api/v2"

        # 生成时间戳 (格式: YYYYMMDD)
        timestamp = datetime.now().strftime('%Y%m%d')

        # Android豆瓣客户端User-Agent
        user_agent = "api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 product/Mate 40 vendor/HUAWEI model/Mate 40 brand/HUAWEI  rom/android  network/wifi  platform/AndroidPad"

        # 尝试movie和tv两种类型
        data = None
        for media_type in ['movie', 'tv']:
            url_path = f"/api/v2/{media_type}/{douban_id}"

            # 生成签名
            signature = self._generate_douban_signature(url_path, timestamp)

            # 构造完整URL
            api_url = f"{base_url}/{media_type}/{douban_id}"

            # 构造请求参数
            params = {
                "apiKey": api_key,
                "os_rom": "android",
                "_ts": timestamp,
                "_sig": signature
            }

            self.logger.info(f"豆瓣: 正在从Frodo API获取播放链接 {api_url}")

            try:
                async with await self._create_client() as client:
                    response = await client.get(api_url, params=params, headers={
                        "User-Agent": user_agent,
                        "Referer": "https://frodo.douban.com",
                        "Accept": "*/*",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
                    }, follow_redirects=False)  # 不跟随重定向

                    # 记录原始响应(如果启用)
                    if log_raw:
                        metadata_logger.info(f"豆瓣Frodo API响应 ({media_type}/{douban_id}):\nURL: {api_url}\nStatus: {response.status_code}\nBody:\n{response.text}\n{'='*80}")

                    if response.status_code == 200:
                        data = response.json()
                        break
                    elif response.status_code in [301, 302]:
                        # 如果是重定向,尝试下一个类型
                        continue
                    else:
                        self.logger.error(f"豆瓣: 获取Frodo API失败 (HTTP {response.status_code}): {api_url}")
                        continue

            except Exception as e:
                self.logger.error(f"豆瓣: 请求Frodo API异常 ({media_type}): {e}")
                continue

        if not data:
            self.logger.warning(f"豆瓣: 无法获取播放链接 (douban_id={douban_id})")
            return []

        try:
            # 获取 vendors 列表
            vendors = data.get('vendors', [])

            if not vendors:
                self.logger.warning(f"豆瓣: 未找到播放链接 (douban_id={douban_id})")
                return []

            self.logger.info(f"豆瓣: 找到 {len(vendors)} 个播放平台")

            # 提取播放链接
            episode_urls: List[Tuple[int, str]] = []

            for vendor in vendors:
                vendor_id = vendor.get('id', '')
                vendor_uri = vendor.get('uri', '')

                # 如果指定了目标平台,只处理该平台
                if target_provider:
                    target_vendor_id = provider_to_vendor_id.get(target_provider)
                    if target_vendor_id and vendor_id != target_vendor_id:
                        continue

                if vendor_uri:
                    self.logger.info(f"豆瓣: 找到播放链接 vendor={vendor_id}, uri={vendor_uri}")
                    # 直接返回vendor.uri,由调用方(scraper)的get_id_from_url方法解析
                    episode_urls.append((1, vendor_uri))

            self.logger.info(f"豆瓣: 获取到 {len(episode_urls)} 个播放链接")
            return episode_urls

        except Exception as e:
            self.logger.error(f"豆瓣: 解析播放链接时发生异常: {e}", exc_info=True)
            return []