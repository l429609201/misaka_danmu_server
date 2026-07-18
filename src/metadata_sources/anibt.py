"""AniBT 元信息源：统一标题搜索、详情、单季度探索与私有 RSS 订阅。"""
import asyncio
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import Request

from src.db import crud, models
from .base import BaseMetadataSource


class AniBTMetadataSource(BaseMetadataSource):
    provider_name = "anibt"
    config_keys = ["anibtApiBaseUrl", "anibtImageBaseUrl"]
    bool_config_keys: List[str] = []
    configurable_fields: Dict[str, Any] = {
        "anibtApiBaseUrl": {
            "label": "API 地址",
            "type": "url",
            "tooltip": "AniBT API 服务地址，通常无需修改",
            "placeholder": "https://anibt.net",
            "default": "https://anibt.net",
        },
        "anibtImageBaseUrl": {
            "label": "图片地址",
            "type": "url",
            "tooltip": "可选；留空时使用 API 地址解析相对图片路径",
            "placeholder": "https://anibt.net",
            "default": "",
        },
    }
    test_url = "https://anibt.net"
    supports_subscription = True
    handled_domains = ["anibt.net"]
    subscription_types = [{
        "type": "anibt_rss_feed",
        "label": "AniBT 私有 RSS",
        "description": "粘贴 AniBT 生成的带鉴权密钥 RSS 地址，自动同步其中的追番订阅",
        "payloadSchema": {"fields": [{
            "name": "rssUrl", "type": "password", "required": True,
            "label": "私有 RSS 地址", "placeholder": "https://anibt.net/...",
        }]},
    }]

    async def _base_url(self) -> str:
        return (await self.config_manager.get("anibtApiBaseUrl", "https://anibt.net")).rstrip("/")

    async def _client(self) -> httpx.AsyncClient:
        proxy = None
        proxy_mode = await self.config_manager.get("proxyMode", "none")
        if proxy_mode == "none" and (await self.config_manager.get("proxyEnabled", "false")).lower() == "true":
            proxy_mode = "http_socks"
        if proxy_mode == "http_socks":
            async with self._session_factory() as session:
                setting = await crud.get_metadata_source_setting_by_name(session, self.provider_name)
            if setting and setting.get("useProxy"):
                proxy = await self.config_manager.get("proxyUrl", "") or None
        return httpx.AsyncClient(base_url=await self._base_url(), timeout=15.0, proxy=proxy)

    async def _image_url(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        if value.startswith(("http://", "https://")):
            return value
        image_base = await self.config_manager.get("anibtImageBaseUrl", "")
        return urljoin((image_base or await self._base_url()).rstrip("/") + "/", value.lstrip("/"))

    async def _pick_image(self, item: Dict[str, Any]) -> Optional[str]:
        variants = item.get("coverImageWebp", {}).get("variants") or []
        if variants:
            best = max(variants, key=lambda row: row.get("width") or 0)
            return await self._image_url(best.get("url"))
        cover = item.get("coverImage") or {}
        return await self._image_url(cover.get("extraLarge") or cover.get("large") or item.get("cover") or item.get("image"))

    @staticmethod
    def _media_type(item: Dict[str, Any]) -> str:
        value = str(item.get("format") or item.get("kind") or "").upper()
        if value == "MOVIE":
            return "movie"
        if value in {"TV", "ONA", "OVA"}:
            return "tv_series"
        return "other"

    async def _to_result(self, item: Dict[str, Any]) -> models.MetadataDetailsResponse:
        titles = item.get("title") if isinstance(item.get("title"), dict) else {}
        bgm_id = item.get("bgmId")
        title = (titles.get("chinese") or titles.get("primary") or item.get("nameCn")
                 or titles.get("native") or titles.get("japanese") or item.get("name")
                 or f"AniBT {bgm_id or item.get('_id')}")
        year = item.get("seasonYear")
        if not year and item.get("date"):
            try:
                year = int(str(item["date"])[:4])
            except (TypeError, ValueError):
                year = None
        if not year and item.get("airingAt"):
            try:
                timestamp = float(item["airingAt"])
                if timestamp > 10_000_000_000:
                    timestamp /= 1000
                year = datetime.fromtimestamp(timestamp, tz=timezone.utc).year
            except (TypeError, ValueError, OSError, OverflowError):
                year = None

        # why：AniBT 搜索接口按语言拆分标题，必须完整映射，不能只保留繁体中文一个别名。
        aliases_cn = list(dict.fromkeys(
            value for value in [titles.get("chinese"), titles.get("chineseTraditional"), item.get("nameCn")]
            if value and value != title
        ))
        name_jp = titles.get("japanese") or titles.get("native") or item.get("name")
        aliases_jp = list(dict.fromkeys(
            value for value in [titles.get("japanese"), titles.get("native")]
            if value and value != name_jp
        ))
        genres = item.get("genres") or []
        details = item.get("description") or " / ".join(filter(None, [
            str(item.get("format") or ""),
            f"{item.get('episodes')}集" if item.get("episodes") else "",
            f"评分{item.get('averageScore') or item.get('rating')}" if item.get("averageScore") or item.get("rating") else "",
            "、".join(str(genre) for genre in genres[:5]) if genres else "",
        ]))
        return models.MetadataDetailsResponse(
            id=str(bgm_id or item.get("_id") or item.get("animeId")), provider=self.provider_name,
            title=title, type=self._media_type(item), bangumiId=str(bgm_id) if bgm_id else None,
            nameEn=titles.get("english"), nameJp=name_jp,
            nameRomaji=titles.get("romaji") or item.get("titleRomaji"), aliasesCn=aliases_cn,
            aliasesJp=aliases_jp, imageUrl=await self._pick_image(item),
            details=details, year=year,
            extra={"animeId": item.get("animeId") or item.get("_id"), "format": item.get("format"),
                   "status": item.get("status"), "season": item.get("season"), "genres": genres,
                   "officialSite": item.get("officialSite"), "episodes": item.get("episodes"),
                   "airingAt": item.get("airingAt"), "weekday": item.get("weekday"),
                   "scheduleStatus": item.get("scheduleStatus"),
                   "rssReleaseCount": item.get("rssReleaseCount"), "hasRelease": item.get("hasRelease")},
        )

    @staticmethod
    def _merge_result(base: models.MetadataDetailsResponse, detail: models.MetadataDetailsResponse) -> None:
        """把 AniBT 详情字段合并回搜索结果，保持插件内部自行补全。"""
        base.aliasesCn = list(dict.fromkeys((base.aliasesCn or []) + (detail.aliasesCn or [])))
        base.aliasesJp = list(dict.fromkeys((base.aliasesJp or []) + (detail.aliasesJp or [])))
        for field in ("nameEn", "nameJp", "nameRomaji", "year", "imageUrl"):
            if not getattr(base, field, None) and getattr(detail, field, None):
                setattr(base, field, getattr(detail, field))
        if detail.details and (not base.details or len(detail.details) > len(base.details)):
            base.details = detail.details
        if base.type in ("", "unknown", "other") and detail.type:
            base.type = detail.type
        base.extra = {**(base.extra or {}), **(detail.extra or {})}

    async def _search_endpoint(self, keyword: str, user: models.User) -> List[models.MetadataDetailsResponse]:
        # why：季度接口的 query 是 AniBT 官方统一标题索引，会跨季度匹配全部标题字段。
        async with await self._client() as client:
            response = await client.get("/api/seasons/anime", params={"query": keyword[:120]})
            response.raise_for_status()
            data = response.json().get("data", {})
        rows = [row for group in data.get("byWeekday", []) for row in group.get("animes", [])]
        results = [await self._to_result(row) for row in rows[:50]]

        # why：AniBT 搜索接口偏精简，由插件自己补拉前三个候选详情，不污染通用元数据管理器。
        detail_tasks = [self.get_details(item.bangumiId or item.id, user) for item in results[:3]]
        if detail_tasks:
            details = await asyncio.gather(*detail_tasks, return_exceptions=True)
            for base, detail in zip(results[:3], details):
                if isinstance(detail, models.MetadataDetailsResponse):
                    self._merge_result(base, detail)
        return results

    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        cache_key = f"season_query_v2:{mediaType or 'all'}:{keyword[:120]}"
        cached = await self.cache_manager.get("anibt_search", cache_key)
        if isinstance(cached, list):
            return [models.MetadataDetailsResponse(**row) for row in cached]
        results = await self._search_endpoint(keyword, user)
        if mediaType:
            results = [item for item in results if item.type == mediaType]
        await self.cache_manager.set(
            "anibt_search", cache_key, [r.model_dump() for r in results],
            ttl_seconds=1800 if results else 60,
        )
        return results

    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        bgm_id = str(item_id).removeprefix("bgm:").strip()
        if not bgm_id.isdigit():
            return None
        cache_key = f"v2:{bgm_id}"
        cached = await self.cache_manager.get("anibt_details", cache_key)
        if isinstance(cached, dict):
            return models.MetadataDetailsResponse(**cached)
        async with await self._client() as client:
            response = await client.get("/api/anime/lookup", params={"source": "bgm", "id": bgm_id})
            if response.status_code == 404:
                return None
            response.raise_for_status()
            anime = response.json().get("data", {}).get("anime")
        if not anime:
            return None
        result = await self._to_result(anime)
        await self.cache_manager.set("anibt_details", cache_key, result.model_dump(), ttl_seconds=21600)
        return result

    async def search_aliases(self, keyword: str, user: models.User) -> Set[str]:
        aliases: Set[str] = set()
        for item in (await self.search(keyword, user))[:5]:
            aliases.update(filter(None, [item.title, item.nameEn, item.nameJp, item.nameRomaji]))
            aliases.update(item.aliasesCn or [])
            aliases.update(item.aliasesJp or [])
        return aliases

    async def check_connectivity(self) -> Dict[str, str]:
        try:
            async with await self._client() as client:
                response = await client.get("/api/seasons/anime", params={"query": "test"})
                response.raise_for_status()
            return {"code": "ok", "message": "AniBT 公开 API 连接正常"}
        except Exception as exc:
            return {"code": "error", "message": f"AniBT 连接失败: {exc}"}

    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: models.User, request: Request) -> Any:
        if action_name != "discoverSeason":
            raise NotImplementedError(f"AniBT 未实现操作: {action_name}")
        # why：探索只查询一个季度；不再向前端暴露多季度列表或做多月聚合。
        season = str((payload or {}).get("season") or "").strip()
        params = {"season": season} if season and season != "current" else {}
        async with await self._client() as client:
            response = await client.get("/api/seasons/anime", params=params)
            response.raise_for_status()
            data = response.json().get("data", {})
        items = []
        for group in data.get("byWeekday", []):
            for row in group.get("animes", []):
                result = await self._to_result(row)
                items.append({
                    "provider": self.provider_name, "type": "anibt_season_anime",
                    "title": result.title, "cover": result.imageUrl, "description": result.details,
                    "payload": {"bangumiId": result.bangumiId, "animeId": result.extra.get("animeId"),
                                "season": data.get("requestedSeason"), "hasRelease": result.extra.get("hasRelease")},
                })
        return {"season": data.get("requestedSeason"), "items": items}


    async def check_subscription_capability(self, user=None) -> Dict[str, Any]:
        return {
            "available": True, "authRequired": True, "authStatus": "user_url",
            "reason": "需要用户提供 AniBT 私有 RSS 地址", "subscriptionTypes": self.subscription_types,
        }

    async def validate_subscription_payload(self, subscription_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if subscription_type != "anibt_rss_feed":
            raise ValueError(f"AniBT 暂不支持订阅类型: {subscription_type}")
        rss_url = str((payload or {}).get("rssUrl") or "").strip()
        parsed = urlparse(rss_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("请输入有效的 AniBT RSS 地址")
        if parsed.hostname != "anibt.net" and not parsed.hostname.endswith(".anibt.net"):
            raise ValueError("RSS 地址必须来自 anibt.net")
        # why：externalId 只保存摘要，避免列表、日志和任务标题泄露私有鉴权参数。
        feed_id = hashlib.sha256(rss_url.encode("utf-8")).hexdigest()[:20]
        return {
            "provider": self.provider_name, "externalId": f"rss-{feed_id}",
            "title": "AniBT 私有 RSS", "animeType": "subscription_feed",
            "subscriptionType": "anibt_rss_feed", "extraData": {"rssUrl": rss_url},
        }

    @staticmethod
    def _rss_text(node: ET.Element, name: str) -> str:
        child = next((c for c in node if c.tag.rsplit("}", 1)[-1] == name), None)
        return (child.text or "").strip() if child is not None else ""

    async def scan_subscription_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        """读取私有 RSS，统一返回作品订阅列表；落库由 SubscriptionManager 负责。"""
        if target.get("subscriptionType") != "anibt_rss_feed":
            return {"mode": "noop", "items": []}
        rss_url = str(target.get("rssUrl") or "").strip()
        if not rss_url:
            raise ValueError("订阅目标缺少 RSS 地址")
        async with await self._client() as client:
            response = await client.get(rss_url)
            response.raise_for_status()
        root = ET.fromstring(response.content)
        entries = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
        items = []
        seen = set()
        for entry in entries:
            title = self._rss_text(entry, "title")
            link = self._rss_text(entry, "link")
            if not link:
                link_node = next((c for c in entry if c.tag.rsplit("}", 1)[-1] == "link"), None)
                link = (link_node.attrib.get("href") or "") if link_node is not None else ""
            text = " ".join([title, link, self._rss_text(entry, "guid"), self._rss_text(entry, "id")])
            match = re.search(r"(?:bgm(?:Id)?[=:/-]?|subject/)([0-9]{1,10})", text, re.I)
            if not match or match.group(1) in seen:
                continue
            bgm_id = match.group(1)
            seen.add(bgm_id)
            detail = await self.get_details(bgm_id, models.User(id=0, username="__anibt_rss__"))
            items.append({
                "provider": self.provider_name, "externalId": f"bgm-{bgm_id}",
                "title": detail.title if detail else title, "animeType": detail.type if detail else "tv_series",
                "subscriptionType": "anibt_subject", "status": "pending",
                "extraData": {"bangumiId": bgm_id, "year": detail.year if detail else None,
                              "imageUrl": detail.imageUrl if detail else None},
            })
        return {"mode": "subscriptions", "items": items}
