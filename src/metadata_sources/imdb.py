import asyncio
import logging
import re
import json
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Final, Tuple

import httpx
from pydantic import BaseModel, Field
from fastapi import HTTPException, Request

from src.db import models
from src.db import crud
from .base import BaseMetadataSource

logger = logging.getLogger(__name__)

# Cache lifespan in seconds (24 hours)
CACHE_LIFESPAN: Final[int] = 86400

# --- Enums ---
class ImdbType(Enum):
    """IMDb media types"""
    TV_SERIES = "tvSeries"
    TV_MINI_SERIES = "tvMiniSeries"
    MOVIE = "movie"
    TV_MOVIE = "tvMovie"
    MUSIC_VIDEO = "musicVideo"
    TV_SHORT = "tvShort"
    SHORT = "short"
    TV_EPISODE = "tvEpisode"
    TV_SPECIAL = "tvSpecial"
    VIDEO_GAME = "videoGame"
    VIDEO = "video"
    PODCAST_SERIES = "podcastSeries"
    PODCAST_EPISODE = "podcastEpisode"

# --- Pydantic Models for IMDb JSON API (HTML Parsing) ---
class ImdbApiImage(BaseModel):
    height: int
    imageUrl: str
    width: int

class ImdbApiResultItem(BaseModel):
    id: str
    l: str  # title
    q: Optional[str] = None  # type like "feature"
    s: Optional[str] = None  # actors
    y: Optional[int] = None  # year
    i: Optional[ImdbApiImage] = None

class ImdbApiResponse(BaseModel):
    d: List[ImdbApiResultItem] = []

# --- Pydantic Models for Third-Party IMDb API (api.imdbapi.dev) ---
class ImdbApiDevImage(BaseModel):
    """Image model for IMDb API"""
    url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    type: Optional[str] = None

class ImdbApiDevCountry(BaseModel):
    """Country model for IMDb API"""
    code: Optional[str] = None  # ISO 3166-1 alpha-2
    name: Optional[str] = None

class ImdbApiDevLanguage(BaseModel):
    """Language model for IMDb API"""
    code: Optional[str] = None  # ISO 639-3
    name: Optional[str] = None

class RatingsSummary(BaseModel):
    """Ratings summary model"""
    aggregate_rating: Optional[float] = Field(None, alias='aggregateRating')
    vote_count: Optional[int] = Field(None, alias='voteCount')

class ImdbApiDevTitle(BaseModel):
    """Title model for IMDb API"""
    id: str
    type: ImdbType
    is_adult: Optional[bool] = Field(None, alias='isAdult')
    primary_title: Optional[str] = Field(None, alias='primaryTitle')
    original_title: Optional[str] = Field(None, alias='originalTitle')
    primary_image: Optional[ImdbApiDevImage] = Field(None, alias='primaryImage')
    start_year: Optional[int] = Field(None, alias='startYear')
    end_year: Optional[int] = Field(None, alias='endYear')
    runtime_seconds: Optional[int] = Field(None, alias='runtimeSeconds')
    genres: Optional[List[str]] = None
    rating: Optional[RatingsSummary] = None
    plot: Optional[str] = None
    origin_countries: Optional[List[ImdbApiDevCountry]] = Field(default_factory=list, alias='originCountries')
    spoken_languages: Optional[List[ImdbApiDevLanguage]] = Field(default_factory=list, alias='spokenLanguages')

class ImdbApiDevSearchTitlesResponse(BaseModel):
    """Search response model"""
    titles: List[ImdbApiDevTitle]

class ImdbApiDevAka(BaseModel):
    """Alternative title (AKA) model"""
    text: Optional[str] = ''
    attributes: List[str] = Field(default_factory=list)

class ImdbApiDevListTitleAKAsResponse(BaseModel):
    """AKAs response model"""
    akas: List[ImdbApiDevAka]


class CloudflareBlockedError(Exception):
    """Raised when Cloudflare blocks the request (403)"""
    pass


class ImdbApiDevClient:
    """Client for Third-Party IMDb API (api.imdbapi.dev)"""
    BASE_URL = 'https://api.imdbapi.dev'

    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url
        self.logger = logging.getLogger(self.__class__.__name__)

    async def _make_request(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        """Make a request to the IMDb API

        Raises:
            CloudflareBlockedError: When Cloudflare returns 403 (usually due to geo-blocking)
        """
        try:
            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            async with httpx.AsyncClient(
                timeout=15.0,
                proxy=self.proxy_url,
                follow_redirects=True,
                headers=headers
            ) as client:
                response = await client.get(f"{self.BASE_URL}{path}", params=params)

                # Check for Cloudflare block (403 with challenge page)
                if response.status_code == 403:
                    content_type = response.headers.get("content-type", "")
                    if "text/html" in content_type:
                        # Cloudflare challenge page detected
                        self.logger.warning(f"IMDb API blocked by Cloudflare (403): {path}")
                        raise CloudflareBlockedError("Cloudflare blocked the request - try using a US proxy")
                    else:
                        self.logger.warning(f"IMDb API returned 403: {path}")
                        return None

                if response.status_code != 200:
                    self.logger.warning(f"IMDb API request failed: {path}, status: {response.status_code}")
                    return None
                return response.json()
        except CloudflareBlockedError:
            raise  # Re-raise Cloudflare errors
        except httpx.TimeoutException:
            self.logger.warning(f"IMDb API request timeout: {path}")
            raise CloudflareBlockedError("Request timeout - Cloudflare may be blocking or network issue")
        except Exception as e:
            self.logger.error(f"IMDb API request error: {e}")
            return None

    async def search_titles(self, query: str, limit: Optional[int] = None) -> Optional[ImdbApiDevSearchTitlesResponse]:
        """Search for titles using a query string"""
        path = '/search/titles'
        params: Dict[str, Any] = {'query': query}
        if limit:
            params['limit'] = limit

        try:
            data = await self._make_request(path, params)
            if data is None:
                return None
            return ImdbApiDevSearchTitlesResponse.model_validate(data)
        except Exception as e:
            self.logger.debug(f"Error searching titles: {e}")
            return None

    async def get_title(self, title_id: str) -> Optional[ImdbApiDevTitle]:
        """Retrieve a title's details using its IMDb ID"""
        path = f'/titles/{title_id}'
        try:
            data = await self._make_request(path)
            if data is None:
                return None
            return ImdbApiDevTitle.model_validate(data)
        except Exception as e:
            self.logger.debug(f"Error getting title details: {e}")
            return None

    async def get_akas(self, title_id: str) -> Optional[ImdbApiDevListTitleAKAsResponse]:
        """Retrieve alternative titles (AKAs) for a title"""
        path = f'/titles/{title_id}/akas'
        try:
            data = await self._make_request(path)
            if data is None:
                return None
            return ImdbApiDevListTitleAKAsResponse.model_validate(data)
        except Exception as e:
            self.logger.debug(f"Error getting AKAs: {e}")
            return None


class ImdbMetadataSource(BaseMetadataSource):
    provider_name = "imdb"
    test_url = "https://www.imdb.com"

    # 配置字段 (格式: "config_key": ("UI标签", "字段类型", "提示信息"))
    configurable_fields = {
        "imdbUseApi": ("使用第三方API", "boolean", "使用第三方API (api.imdbapi.dev) 而不是官方网站HTML解析"),
        "imdbEnableFallback": ("启用兜底", "boolean", "当主方式失败时,自动尝试另一种方式")
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._api_dev_client: Optional[ImdbApiDevClient] = None
        self._html_client: Optional[httpx.AsyncClient] = None

    async def _get_config(self, key: str, default: Any = None) -> Any:
        """获取配置值 (从config表读取)"""
        # 配置键映射: 内部键 -> 数据库键
        config_key_map = {
            "useApi": "imdbUseApi",
            "enableFallback": "imdbEnableFallback"
        }

        db_key = config_key_map.get(key, key)
        value_str = await self.config_manager.get(db_key, str(default))

        # 转换为布尔值
        if isinstance(default, bool):
            return value_str.lower() == 'true'
        return value_str

    async def _get_api_dev_client(self) -> ImdbApiDevClient:
        """Get or create the third-party IMDb API client"""
        if self._api_dev_client is None:
            proxy_url = await self.config_manager.get("proxyUrl", "")
            proxy_enabled_globally = (await self.config_manager.get("proxyEnabled", "false")).lower() == 'true'

            async with self._session_factory() as session:
                metadata_settings = await crud.get_all_metadata_source_settings(session)

            provider_setting = next((s for s in metadata_settings if s['providerName'] == self.provider_name), None)
            use_proxy_for_this_provider = provider_setting.get('useProxy', False) if provider_setting else False

            proxy_to_use = proxy_url if proxy_enabled_globally and use_proxy_for_this_provider and proxy_url else None
            self._api_dev_client = ImdbApiDevClient(proxy_url=proxy_to_use)

        return self._api_dev_client

    async def _get_html_client(self) -> httpx.AsyncClient:
        """Get or create the HTML parsing client"""
        if self._html_client is None:
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

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            }
            self._html_client = httpx.AsyncClient(
                headers=headers,
                timeout=20.0,
                follow_redirects=True,
                proxy=proxy_to_use
            )

        return self._html_client

    def _map_imdb_type_to_media_type(self, imdb_type: ImdbType) -> Optional[str]:
        """Map IMDb type to our media type"""
        type_mapping = {
            ImdbType.MOVIE: "movie",
            ImdbType.TV_MOVIE: "movie",
            ImdbType.TV_SERIES: "tv",
            ImdbType.TV_MINI_SERIES: "tv",
            ImdbType.TV_SPECIAL: "tv",
        }
        return type_mapping.get(imdb_type)

    async def _search_via_html(self, keyword: str, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        """使用HTML解析方式搜索 (官方网站)"""
        self.logger.info(f"IMDb: 正在使用HTML解析搜索 '{keyword}'")
        formatted_keyword = keyword.strip().lower()
        if not formatted_keyword:
            return []

        search_url = f"https://v3.sg.media-imdb.com/suggestion/titles/x/{formatted_keyword}.json"
        try:
            client = await self._get_html_client()
            response = await client.get(search_url)
            response.raise_for_status()
            data = ImdbApiResponse.model_validate(response.json())

            results = []
            for item in data.d:
                if item.q not in ["feature", "tvSeries", "tvMovie", "tvMiniSeries", "video", "tvSpecial"]:
                    continue

                details_parts = []
                if item.y:
                    details_parts.append(f"年份: {item.y}")
                if item.s:
                    details_parts.append(f"演员: {item.s}")

                results.append(models.MetadataDetailsResponse(
                    id=item.id,
                    imdbId=item.id,
                    title=item.l,
                    details=" / ".join(details_parts),
                    imageUrl=item.i.imageUrl if item.i else None
                ))
            return results
        except httpx.ConnectError:
            self.logger.error(f"IMDb HTML 搜索失败: 无法连接到服务器")
            raise HTTPException(status_code=503, detail="无法连接到 IMDb 服务,请检查网络或代理设置")
        except httpx.TimeoutException:
            self.logger.error(f"IMDb HTML 搜索失败: 请求超时")
            raise HTTPException(status_code=504, detail="连接 IMDb 服务超时")
        except httpx.HTTPStatusError as e:
            self.logger.error(f"IMDb HTML 搜索失败: HTTP 状态码 {e.response.status_code}")
            raise HTTPException(status_code=502, detail=f"IMDb 服务返回错误状态码: {e.response.status_code}")
        except Exception as e:
            self.logger.error(f"IMDb HTML 搜索失败: {e}")
            raise HTTPException(status_code=500, detail=f"IMDb HTML 搜索失败: {e}")

    async def _get_details_via_html(self, item_id: str, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        """使用HTML解析方式获取详情 (官方网站)"""
        self.logger.info(f"IMDb: 正在使用HTML解析获取详情 item_id={item_id}")
        details_url = f"https://www.imdb.com/title/{item_id}/"
        try:
            client = await self._get_html_client()
            response = await client.get(details_url)
            response.raise_for_status()
            html = response.text

            name_en: Optional[str] = None
            aliases_cn: List[str] = []
            year = None

            # 尝试解析 __NEXT_DATA__
            next_data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
            if next_data_match:
                try:
                    next_data = json.loads(next_data_match.group(1))
                    main_data = next_data.get("props", {}).get("pageProps", {}).get("mainColumnData", {})

                    name_en = main_data.get("titleText", {}).get("text")
                    original_title = main_data.get("originalTitleText", {}).get("text")

                    # 提取年份信息
                    release_year = main_data.get("releaseYear", {})
                    if release_year and release_year.get("year"):
                        try:
                            year = int(release_year["year"])
                        except (ValueError, TypeError):
                            pass

                    akas = main_data.get("akas", {})
                    if akas and akas.get("edges"):
                        for edge in akas["edges"]:
                            node = edge.get("node", {})
                            if node.get("text"):
                                aliases_cn.append(node["text"])

                    if name_en and original_title and name_en != original_title:
                        aliases_cn.append(original_title)

                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    self.logger.warning(f"解析 IMDb __NEXT_DATA__ 失败,将回退到正则匹配。错误: {e}")
                    name_en = None

            # 如果 __NEXT_DATA__ 解析失败,则回退到正则
            if not name_en:
                self.logger.info(f"IMDb: 正在为 item_id={item_id} 使用正则回退方案")
                title_match = re.search(r'<h1.*?><span.*?>(.*?)</span></h1>', html)
                name_en = title_match.group(1).strip() if title_match else None

                # 如果年份还没有获取到,尝试正则提取
                if not year:
                    year_match = re.search(r'<span class="sc-.*?">(\d{4})</span>', html)
                    if year_match:
                        try:
                            year = int(year_match.group(1))
                        except (ValueError, TypeError):
                            pass

                # 仅在回退模式下使用正则解析别名
                akas_section_match = re.search(r'<div data-testid="akas".*?>(.*?)</div>', html, re.DOTALL)
                if akas_section_match:
                    alias_matches = re.findall(r'<li.*?<a.*?>(.*?)</a>', akas_section_match.group(1), re.DOTALL)
                    aliases_cn.extend([alias.strip() for alias in alias_matches])

            # 在创建响应对象之前进行最终检查
            if not name_en:
                self.logger.error(f"IMDb: 无法从详情页解析出标题 (item_id={item_id})")
                return None

            return models.MetadataDetailsResponse(
                id=item_id,
                imdbId=item_id,
                title=name_en,
                nameEn=name_en,
                year=year,
                aliasesCn=list(dict.fromkeys(filter(None, aliases_cn)))
            )
        except Exception as e:
            self.logger.error(f"解析 IMDb 详情页时发生错误: {e}")
            return None

    async def _search_via_api(self, keyword: str, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        """使用第三方API搜索 (api.imdbapi.dev)

        Raises:
            CloudflareBlockedError: When Cloudflare blocks the request
        """
        self.logger.info(f"IMDb: 正在使用第三方API搜索 '{keyword}'")
        formatted_keyword = keyword.strip()
        if not formatted_keyword:
            return []

        try:
            client = await self._get_api_dev_client()
            response = await client.search_titles(formatted_keyword, limit=20)

            if not response or not response.titles:
                return []

            results = []
            for title in response.titles:
                # Filter by media type if specified
                if mediaType:
                    title_media_type = self._map_imdb_type_to_media_type(title.type)
                    if title_media_type != mediaType:
                        continue

                # Build details string
                details_parts = []
                if title.start_year:
                    details_parts.append(f"年份: {title.start_year}")
                if title.genres:
                    details_parts.append(f"类型: {', '.join(title.genres[:3])}")
                if title.rating and title.rating.aggregate_rating:
                    details_parts.append(f"评分: {title.rating.aggregate_rating:.1f}")

                results.append(models.MetadataDetailsResponse(
                    id=title.id,
                    imdbId=title.id,
                    title=title.primary_title or title.original_title or "",
                    type=self._map_imdb_type_to_media_type(title.type),
                    year=title.start_year,
                    details=" / ".join(details_parts),
                    imageUrl=title.primary_image.url if title.primary_image else None
                ))

            return results

        except CloudflareBlockedError:
            raise  # Re-raise to trigger fallback
        except Exception as e:
            self.logger.error(f"IMDb 第三方API 搜索失败: {e}")
            raise HTTPException(status_code=500, detail=f"IMDb 第三方API 搜索失败: {str(e)}")

    async def _get_details_via_api(self, item_id: str, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        """使用第三方API获取详情 (api.imdbapi.dev)

        Raises:
            CloudflareBlockedError: When Cloudflare blocks the request
        """
        self.logger.info(f"IMDb: 正在使用第三方API获取详情 item_id={item_id}")

        try:
            client = await self._get_api_dev_client()

            # Get title details
            title = await client.get_title(item_id)
            if not title:
                self.logger.error(f"IMDb: 无法获取标题详情 (item_id={item_id})")
                return None

            # Get alternative titles (AKAs)
            aliases_cn: List[str] = []
            akas_response = await client.get_akas(item_id)
            if akas_response and akas_response.akas:
                for aka in akas_response.akas:
                    if aka.text:
                        aliases_cn.append(aka.text)

            # Add original title if different from primary title
            if title.original_title and title.primary_title and title.original_title != title.primary_title:
                aliases_cn.append(title.original_title)

            # Remove duplicates while preserving order
            aliases_cn = list(dict.fromkeys(filter(None, aliases_cn)))

            return models.MetadataDetailsResponse(
                id=item_id,
                imdbId=item_id,
                title=title.primary_title or title.original_title or "",
                nameEn=title.primary_title or title.original_title,
                type=self._map_imdb_type_to_media_type(title.type),
                year=title.start_year,
                aliasesCn=aliases_cn,
                imageUrl=title.primary_image.url if title.primary_image else None
            )

        except CloudflareBlockedError:
            raise  # Re-raise to trigger fallback
        except Exception as e:
            self.logger.error(f"获取 IMDb 第三方API详情时发生错误: {e}")
            return None

    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        """搜索标题 (支持双模式和兜底，遇到Cloudflare 403自动降级)"""
        use_api = await self._get_config("useApi", True)
        enable_fallback = await self._get_config("enableFallback", True)

        self.logger.info(f"IMDb搜索配置: useApi={use_api}, enableFallback={enable_fallback}")

        primary_method = self._search_via_api if use_api else self._search_via_html
        fallback_method = self._search_via_html if use_api else self._search_via_api

        method_name = "第三方API" if use_api else "官方HTML"
        self.logger.info(f"IMDb使用主方式: {method_name}")

        try:
            results = await primary_method(keyword, mediaType)
            if not results and enable_fallback:
                # 如果主方式没有结果,尝试兜底方式
                fallback_name = "官方HTML" if use_api else "第三方API"
                self.logger.info(f"主方式无结果,尝试兜底方式: {fallback_name}")
                try:
                    results = await fallback_method(keyword, mediaType)
                except Exception as fallback_e:
                    self.logger.warning(f"兜底方式也失败: {fallback_e}")
            return results
        except CloudflareBlockedError as cf_e:
            # Cloudflare 阻止，自动降级到 HTML 解析
            if enable_fallback:
                self.logger.warning(f"Cloudflare阻止API访问,自动降级到HTML解析: {cf_e}")
                try:
                    return await self._search_via_html(keyword, mediaType)
                except Exception as fallback_e:
                    self.logger.error(f"HTML解析也失败: {fallback_e}")
                    raise
            else:
                self.logger.error(f"Cloudflare阻止API访问,且未启用兜底: {cf_e}")
                raise HTTPException(status_code=503, detail="IMDb API被Cloudflare阻止,请启用兜底或使用美国代理")
        except Exception as e:
            if enable_fallback:
                fallback_name = "官方HTML" if use_api else "第三方API"
                self.logger.warning(f"主搜索方式失败,尝试兜底方式 {fallback_name}: {e}")
                try:
                    return await fallback_method(keyword, mediaType)
                except Exception as fallback_e:
                    self.logger.error(f"兜底搜索方式也失败: {fallback_e}")
                    raise
            else:
                raise

    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        """获取详情 (支持双模式和兜底，遇到Cloudflare 403自动降级)"""
        use_api = await self._get_config("useApi", True)
        enable_fallback = await self._get_config("enableFallback", True)

        primary_method = self._get_details_via_api if use_api else self._get_details_via_html
        fallback_method = self._get_details_via_html if use_api else self._get_details_via_api

        try:
            result = await primary_method(item_id, mediaType)
            if result:
                return result
            elif enable_fallback:
                self.logger.warning(f"主详情方式返回空,尝试兜底方式")
                return await fallback_method(item_id, mediaType)
            else:
                return None
        except CloudflareBlockedError as cf_e:
            # Cloudflare 阻止，自动降级到 HTML 解析
            if enable_fallback:
                self.logger.warning(f"Cloudflare阻止API访问,自动降级到HTML解析: {cf_e}")
                try:
                    return await self._get_details_via_html(item_id, mediaType)
                except Exception as fallback_e:
                    self.logger.error(f"HTML解析也失败: {fallback_e}")
                    return None
            else:
                self.logger.error(f"Cloudflare阻止API访问,且未启用兜底: {cf_e}")
                return None
        except Exception as e:
            if enable_fallback:
                self.logger.warning(f"主详情方式失败,尝试兜底方式: {e}")
                try:
                    return await fallback_method(item_id, mediaType)
                except Exception as fallback_e:
                    self.logger.error(f"兜底详情方式也失败: {fallback_e}")
                    return None
            else:
                self.logger.error(f"获取 IMDb 详情时发生错误: {e}")
                return None

    async def search_aliases(self, keyword: str, user: models.User) -> Set[str]:
        self.logger.info(f"IMDb: 正在为 '{keyword}' 搜索别名")
        local_aliases: Set[str] = set()
        try:
            search_results = await self.search(keyword, user)
            if not search_results:
                return local_aliases

            best_match_id = search_results[0].id
            details = await self.get_details(best_match_id, user)

            if details:
                if details.nameEn:
                    local_aliases.add(details.nameEn)
                if details.aliasesCn:
                    local_aliases.update(details.aliasesCn)

            self.logger.info(f"IMDb辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
        except Exception as e:
            self.logger.warning(f"IMDb辅助搜索失败: {e}")

        return {alias for alias in local_aliases if alias}

    async def check_connectivity(self) -> Dict[str, str]:
        """检查IMDb源配置状态"""
        try:
            use_api = await self._get_config("useApi", True)
            enable_fallback = await self._get_config("enableFallback", True)

            mode = "第三方API模式 (api.imdbapi.dev)" if use_api else "官方HTML解析模式"
            fallback = "，失败时自动切换" if enable_fallback else ""
            return {"code": "ok", "message": f"{mode}{fallback}"}
        except Exception as e:
            return {"code": "error", "message": f"配置检查失败: {e}"}

    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: models.User, request: Request) -> Any:
        """IMDb source does not support custom actions."""
        raise NotImplementedError(f"源 '{self.provider_name}' 不支持任何自定义操作。")