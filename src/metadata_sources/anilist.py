# -*- coding: utf-8 -*-
"""AniList 元数据源：基于 AniList 公开 GraphQL API，并用 soruly/anilist-chinese 数据集注入中文标题。"""
import asyncio
import html
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from fastapi import Request

from src.schemas import MetadataDetailsResponse, User
from src.services.service_container import get_database_service
from src.utils.misc.converter import convert_title_t2s
from src._version import APP_VERSION
from .base import BaseMetadataSource


# 规范化 UA：如实声明弹幕库身份与版本，不伪装浏览器。
_ANILIST_UA = f"Misaka_Danmu_Server/{APP_VERSION} (https://github.com/l429609201/misaka_danmu_server)"
_ANILIST_API = "https://graphql.anilist.co"
# 社区维护的 AniList 中文译名数据集（繁体，按 AniList ID 索引）
_CHINESE_DATASET_URL = "https://raw.githubusercontent.com/soruly/anilist-chinese/master/anilist-chinese.json"

_CACHE_REGION = "anilist"
_RESULT_TTL = 6 * 3600
_DATASET_TTL = 24 * 3600
_PER_PAGE = 10

# 搜索与详情共用的 Media 字段（参考 MoviePilot anilist 模块）
_MEDIA_FIELDS = """
    id
    idMal
    title { romaji english native }
    format
    status
    description(asHtml: false)
    startDate { year month day }
    season
    seasonYear
    episodes
    coverImage { extraLarge large }
    bannerImage
    synonyms
    averageScore
    siteUrl
    isAdult
"""

_SEARCH_QUERY = f"""
query ($search: String, $perPage: Int) {{
  Page(page: 1, perPage: $perPage) {{
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{
      {_MEDIA_FIELDS}
    }}
  }}
}}
"""

# 按 ID 批量查询：用于中文关键词经本地译名数据集命中后回查 AniList
_IDS_QUERY = f"""
query ($ids: [Int], $perPage: Int) {{
  Page(page: 1, perPage: $perPage) {{
    media(id_in: $ids, type: ANIME) {{
      {_MEDIA_FIELDS}
    }}
  }}
}}
"""

_DETAIL_QUERY = f"""
query ($id: Int) {{
  Media(id: $id, type: ANIME) {{
    {_MEDIA_FIELDS}
  }}
}}
"""

_HTML_TAG_RE = re.compile(r"<[^>]+>")
# 日文假名检测：用于把 synonyms 中的日文别名归入 aliasesJp
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
# 中文/汉字检测：数据集或 synonyms 中的汉字别名归入 aliasesCn
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class AniListMetadataSource(BaseMetadataSource):
    """AniList 元数据源（无需 API Key）。"""

    provider_name = "anilist"
    config_keys: List[str] = []
    bool_config_keys: List[str] = []
    configurable_fields: Dict[str, Any] = {}
    test_url = "https://graphql.anilist.co"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """初始化数据源，并准备中文译名数据集的进程内缓存。"""
        super().__init__(*args, **kwargs)
        # why：数据集约 876KB，进程内缓存避免每次搜索都反序列化 Redis 大值
        self._chinese_map: Optional[Dict[str, Dict[str, Any]]] = None
        self._chinese_lock = asyncio.Lock()
        # why：GitHub 不可达时避免每次搜索都等待 15s 超时，失败后 10 分钟内直接降级
        self._chinese_retry_after = 0.0
        # 中文名检索索引：[(anilist_id, [简体小写名称...])]，由数据集惰性构建
        self._chinese_index: Optional[List[Tuple[str, List[str]]]] = None

    async def _get_proxy(self) -> Optional[str]:
        """按全局代理模式与本源 useProxy 设置决定是否走代理。"""
        proxy_mode = await self.config_service.get("proxyMode", "none")
        if proxy_mode == "none" and (await self.config_service.get("proxyEnabled", "false")).lower() == "true":
            proxy_mode = "http_socks"
        if proxy_mode != "http_socks":
            return None
        db = get_database_service()
        setting = await db.metadata_source.get_metadata_source_setting_by_name(self.provider_name)
        if setting and setting.get("useProxy"):
            return await self.config_service.get("proxyUrl", "") or None
        return None

    async def _client(self) -> httpx.AsyncClient:
        """创建带 UA、超时与代理的 httpx 客户端。"""
        return httpx.AsyncClient(
            timeout=15.0, proxy=await self._get_proxy(),
            headers={"User-Agent": _ANILIST_UA, "Accept": "application/json"},
        )

    async def _graphql(self, query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """执行 GraphQL 请求；限流（429）或请求失败时记录日志并返回 None，不抛出。"""
        try:
            async with await self._client() as client:
                response = await client.post(_ANILIST_API, json={"query": query, "variables": variables})
            if response.status_code == 429:
                self.logger.warning(
                    f"AniList 触发限流(429)，Retry-After={response.headers.get('Retry-After')}，本次跳过"
                )
                return None
            # why：AniList 对不存在的 ID 返回 404 + errors，属于正常的"查无结果"
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.logger.error(f"AniList 请求失败: {exc}")
            return None
        if payload.get("errors"):
            self.logger.warning(f"AniList 返回错误: {payload['errors']}")
        return payload.get("data")

    async def _load_chinese_map(self) -> Dict[str, Dict[str, Any]]:
        """加载中文译名数据集（内存 → 缓存 → 网络），失败时返回空映射，绝不影响搜索。"""
        if self._chinese_map is not None:
            return self._chinese_map
        async with self._chinese_lock:
            if self._chinese_map is not None:
                return self._chinese_map
            if time.monotonic() < self._chinese_retry_after:
                return {}
            cached = await self._cache_get(_CACHE_REGION, "chinese_dataset")
            if isinstance(cached, dict) and cached:
                self._chinese_map = cached
                return cached
            mapping: Dict[str, Dict[str, Any]] = {}
            try:
                async with await self._client() as client:
                    response = await client.get(_CHINESE_DATASET_URL)
                    response.raise_for_status()
                    rows = response.json()
                for row in rows or []:
                    if isinstance(row, dict) and row.get("id"):
                        mapping[str(row["id"])] = {
                            "title": row.get("title") or "",
                            "synonyms": [s for s in (row.get("synonyms") or []) if isinstance(s, str) and s],
                        }
            except Exception as exc:
                # why：失败不写入内存/缓存，冷却期过后可重试
                self.logger.warning(f"AniList 中文译名数据集加载失败，将使用原始标题: {exc}")
                self._chinese_retry_after = time.monotonic() + 600
                return {}
            if mapping:
                self._chinese_map = mapping
                await self._cache_set(_CACHE_REGION, "chinese_dataset", mapping, _DATASET_TTL)
            return mapping

    @staticmethod
    def _normalize(text: str) -> str:
        """中文名比较用的归一化：繁转简、去空白、转小写。"""
        return re.sub(r"\s+", "", convert_title_t2s(text or "")).lower()

    def _match_chinese_ids(self, keyword: str, chinese_map: Dict[str, Dict[str, Any]]) -> List[str]:
        """在中文译名数据集中按关键词匹配 AniList ID（完全匹配 > 前缀 > 包含，短名优先）。"""
        if not chinese_map:
            return []
        if self._chinese_index is None:
            # why：OpenCC 转换 8000+ 条目有成本，只构建一次
            self._chinese_index = [
                (anilist_id, [self._normalize(n) for n in [row.get("title") or ""] + list(row.get("synonyms") or []) if n])
                for anilist_id, row in chinese_map.items()
            ]
        needle = self._normalize(keyword)
        if not needle:
            return []
        scored: List[Tuple[int, int, str]] = []
        for anilist_id, names in self._chinese_index:
            best: Optional[Tuple[int, int]] = None
            for name in names:
                if name == needle:
                    rank = 0
                elif name.startswith(needle):
                    rank = 1
                elif needle in name:
                    rank = 2
                else:
                    continue
                if best is None or (rank, len(name)) < best:
                    best = (rank, len(name))
            if best is not None:
                scored.append((best[0], best[1], anilist_id))
        scored.sort()
        return [anilist_id for _, _, anilist_id in scored[:_PER_PAGE]]

    @staticmethod
    def _media_type(fmt: Optional[str]) -> str:
        """AniList format → 项目内统一媒体类型。"""
        if fmt == "MOVIE":
            return "movie"
        if fmt in ("OVA", "ONA", "SPECIAL"):
            return "ova"
        return "tv_series"

    @staticmethod
    def _strip_html(text: Optional[str]) -> Optional[str]:
        """去除简介中的 HTML 标签与实体。"""
        if not text:
            return None
        cleaned = html.unescape(_HTML_TAG_RE.sub("", text.replace("<br>", "\n"))).strip()
        return cleaned or None

    @staticmethod
    def _dedupe(values: List[str], exclude: Set[str]) -> List[str]:
        """按顺序去重并排除指定值。"""
        result: List[str] = []
        seen = set(exclude)
        for value in values:
            value = (value or "").strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def _to_result(self, media: Dict[str, Any], chinese_map: Dict[str, Dict[str, Any]]) -> MetadataDetailsResponse:
        """把 AniList Media 节点转换为 MetadataDetailsResponse。"""
        anilist_id = str(media.get("id"))
        titles = media.get("title") or {}
        native, romaji, english = titles.get("native"), titles.get("romaji"), titles.get("english")
        translation = chinese_map.get(anilist_id) or {}
        zh_title_tw = translation.get("title") or ""
        # 数据集为繁体，主标题转简体以对齐项目内中文检索；繁体原文保留在别名中
        zh_title = convert_title_t2s(zh_title_tw) if zh_title_tw else ""
        title = zh_title or native or romaji or english or anilist_id

        cn_candidates: List[str] = [zh_title_tw]
        for synonym in translation.get("synonyms") or []:
            cn_candidates.extend([convert_title_t2s(synonym), synonym])
        jp_candidates: List[str] = []
        other_synonyms: List[str] = []
        # why：AniList synonyms 混有多语种译名，仅日文/汉字进入对应别名，其余放 extra 避免污染
        for synonym in media.get("synonyms") or []:
            if _KANA_RE.search(synonym):
                jp_candidates.append(synonym)
            elif _CJK_RE.search(synonym):
                cn_candidates.append(synonym)
            else:
                other_synonyms.append(synonym)
        aliases_cn = self._dedupe(cn_candidates, {title})
        aliases_jp = self._dedupe(jp_candidates, {title, native or "", romaji or "", english or ""})

        cover = media.get("coverImage") or {}
        start = media.get("startDate") or {}
        return MetadataDetailsResponse(
            id=anilist_id,
            title=title,
            type=self._media_type(media.get("format")),
            nameEn=english,
            nameJp=native,
            nameRomaji=romaji,
            aliasesCn=aliases_cn,
            aliasesJp=aliases_jp,
            imageUrl=cover.get("extraLarge") or cover.get("large"),
            details=self._strip_html(media.get("description")),
            year=start.get("year") or media.get("seasonYear"),
            provider=self.provider_name,
            extra={
                "idMal": media.get("idMal"),
                "format": media.get("format"),
                "episodes": media.get("episodes"),
                "bannerImage": media.get("bannerImage"),
                "siteUrl": media.get("siteUrl"),
                "averageScore": media.get("averageScore"),
                "synonyms": other_synonyms,
            },
        )

    @staticmethod
    def _filter_by_media_type(results: List[MetadataDetailsResponse], mediaType: Optional[str]) -> List[MetadataDetailsResponse]:
        """按调用方期望的媒体类型过滤：movie 仅保留剧场版，tv/tv_series 排除剧场版。"""
        if mediaType == "movie":
            return [r for r in results if r.type == "movie"]
        if mediaType in ("tv", "tv_series"):
            return [r for r in results if r.type != "movie"]
        return results

    async def search(self, keyword: str, user: User, mediaType: Optional[str] = None) -> List[MetadataDetailsResponse]:
        """按关键词搜索 AniList 动画条目。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        cache_key = f"search_{keyword}"
        cached = await self._cache_get(_CACHE_REGION, cache_key)
        if isinstance(cached, list):
            results = [MetadataDetailsResponse.model_validate(row) for row in cached]
            return self._filter_by_media_type(results, mediaType)

        chinese_map = await self._load_chinese_map()
        media_list: List[Dict[str, Any]] = []
        # why：AniList 自身搜索不索引中文名，中文关键词需先经译名数据集定位 ID 再回查
        if _CJK_RE.search(keyword) and not _KANA_RE.search(keyword):
            matched_ids = self._match_chinese_ids(keyword, chinese_map)
            if matched_ids:
                id_data = await self._graphql(_IDS_QUERY, {"ids": [int(i) for i in matched_ids], "perPage": _PER_PAGE})
                by_id = {str(m.get("id")): m for m in ((id_data or {}).get("Page") or {}).get("media") or [] if m}
                # id_in 不保证顺序，按本地匹配排名重排
                media_list.extend(by_id[i] for i in matched_ids if i in by_id)
        data = await self._graphql(_SEARCH_QUERY, {"search": keyword, "perPage": _PER_PAGE})
        if data is None and not media_list:
            return []
        seen_ids = {str(m.get("id")) for m in media_list}
        for media in ((data or {}).get("Page") or {}).get("media") or []:
            if media and media.get("id") and str(media["id"]) not in seen_ids:
                seen_ids.add(str(media["id"]))
                media_list.append(media)
        results = [self._to_result(media, chinese_map) for media in media_list if media and media.get("id")]
        await self._cache_set(_CACHE_REGION, cache_key, [r.model_dump() for r in results], _RESULT_TTL)
        return self._filter_by_media_type(results, mediaType)

    async def get_details(self, item_id: str, user: User, mediaType: Optional[str] = None) -> Optional[MetadataDetailsResponse]:
        """按 AniList ID 获取动画详情。"""
        try:
            anilist_id = int(str(item_id).strip())
        except (TypeError, ValueError):
            self.logger.warning(f"AniList 详情 ID 非法: {item_id}")
            return None
        cache_key = f"detail_{anilist_id}"
        cached = await self._cache_get(_CACHE_REGION, cache_key)
        if isinstance(cached, dict):
            return MetadataDetailsResponse.model_validate(cached)

        data = await self._graphql(_DETAIL_QUERY, {"id": anilist_id})
        media = (data or {}).get("Media")
        if not media:
            return None
        result = self._to_result(media, await self._load_chinese_map())
        await self._cache_set(_CACHE_REGION, cache_key, result.model_dump(), _RESULT_TTL)
        return result

    async def search_aliases(self, keyword: str, user: User) -> Set[str]:
        """从前几条搜索结果中收集所有名称作为别名。"""
        aliases: Set[str] = set()
        for item in (await self.search(keyword, user))[:5]:
            aliases.update(filter(None, [item.title, item.nameEn, item.nameJp, item.nameRomaji]))
            aliases.update(item.aliasesCn or [])
            aliases.update(item.aliasesJp or [])
        return aliases

    async def check_connectivity(self) -> Dict[str, str]:
        """检测 AniList GraphQL API 连通性。"""
        try:
            async with await self._client() as client:
                response = await client.post(
                    _ANILIST_API, json={"query": "query { Media(id: 1, type: ANIME) { id } }"},
                )
            if response.status_code == 429:
                return {"code": "ok", "message": "AniList 可访问（当前触发限流）"}
            response.raise_for_status()
            return {"code": "ok", "message": "AniList 公开 API 连接正常"}
        except Exception as exc:
            return {"code": "error", "message": f"AniList 连接失败: {exc}"}

    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: User, request: Request) -> Any:
        """AniList 暂无自定义操作。"""
        raise NotImplementedError(f"AniList 未实现操作: {action_name}")
