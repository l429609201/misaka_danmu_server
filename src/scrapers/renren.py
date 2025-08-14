from __future__ import annotations

import logging
import asyncio
import base64
import hmac
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional
from urllib.parse import urlencode, urlparse

import aiomysql
import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from pydantic import BaseModel, Field, field_validator

from ..config_manager import ConfigManager
from .. import models
from .base import BaseScraper, get_season_from_title

scraper_responses_logger = logging.getLogger("scraper_responses")

# =====================
#  Common utils (ported from the previous standalone script and adapted)
# =====================

AES_KEY: bytes = b"3b744389882a4067"
SIGN_SECRET: str = "ES513W0B1CsdUrR13Qk5EgDAKPeeKZY"
BASE_API = "https://api.rrmj.plus"


@dataclass(frozen=True)
class ClientProfile:
    client_type: str = "web_pc"
    client_version: str = "1.0.0"
    user_agent: str = "Mozilla/5.0"
    origin: str = "https://rrsp.com.cn"
    referer: str = "https://rrsp.com.cn/"


def _sorted_query_string(params: Mapping[str, Any] | None) -> str:
    if not params:
        return ""
    normalized: dict[str, str] = {}
    for k, v in params.items():
        if isinstance(v, bool):
            normalized[k] = "true" if v else "false"
        elif v is None:
            normalized[k] = ""
        else:
            normalized[k] = str(v)
    return urlencode(sorted(normalized.items()))


def _generate_signature(
    method: str,
    ali_id: str,
    ct: str,
    cv: str,
    timestamp_ms: int,
    path: str,
    sorted_query: str,
    secret: str,
) -> str:
    sign_str = f"""{method.upper()}\naliId:{ali_id}\nct:{ct}\ncv:{cv}\nt:{timestamp_ms}\n{path}?{sorted_query}"""
    signature = hmac.new(secret.encode(), sign_str.encode(), digestmod="sha256").digest()
    return base64.b64encode(signature).decode()


def build_signed_headers(
    *,
    method: str,
    url: str,
    params: Mapping[str, Any] | None,
    device_id: str,
    profile: ClientProfile | None = None,
    token: str | None = None,
) -> dict[str, str]:
    prof = profile or ClientProfile()
    parsed = urlparse(url)
    sorted_query = _sorted_query_string(params)
    now_ms = int(time.time() * 1000)
    x_ca_sign = _generate_signature(
        method=method,
        ali_id=device_id,
        ct=prof.client_type,
        cv=prof.client_version,
        timestamp_ms=now_ms,
        path=parsed.path,
        sorted_query=sorted_query,
        secret=SIGN_SECRET,
    )

    return {
        "clientVersion": prof.client_version,
        "deviceId": device_id,
        "clientType": prof.client_type,
        "t": str(now_ms),
        "aliId": device_id,
        "umid": device_id,
        "token": token or "",
        "cv": prof.client_version,
        "ct": prof.client_type,
        "uet": "9",
        "x-ca-sign": x_ca_sign,
        "Accept": "application/json",
        "User-Agent": prof.user_agent,
        "Origin": prof.origin,
        "Referer": prof.referer,
    }


def aes_ecb_pkcs7_decrypt_base64(cipher_b64: str) -> str:
    raw = base64.b64decode(cipher_b64)
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    plain = unpad(cipher.decrypt(raw), AES.block_size)
    return plain.decode("utf-8")


def auto_decode(payload: str) -> Any:
    text = payload.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        decrypted = aes_ecb_pkcs7_decrypt_base64(text)
        try:
            return json.loads(decrypted)
        except Exception:
            return decrypted
    except Exception:
        return text


# =====================
#  Pydantic models for Renren
# =====================


class RrspSearchDramaInfo(BaseModel):
    id: str
    title: str
    year: Optional[int] = None
    cover: Optional[str] = None
    episode_total: Optional[int] = Field(None, alias="episodeTotal")

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v):
        return str(v) if v is not None else ""


class RrspSearchData(BaseModel):
    searchDramaList: List[RrspSearchDramaInfo] = Field(default_factory=list)


class RrspSearchResult(BaseModel):
    data: RrspSearchData


class RrspEpisodeInfo(BaseModel):
    sid: str
    order: int
    title: str


class RrspDramaInfo(BaseModel):
    dramaId: str = Field(alias="dramaId")
    title: str

    @field_validator("dramaId", mode="before")
    @classmethod
    def _coerce_drama_id(cls, v):
        return str(v) if v is not None else ""


class RrspDramaDetail(BaseModel):
    dramaInfo: RrspDramaInfo
    episodeList: List[Dict[str, Any]] = Field(default_factory=list)


class RrspDramaDetailEnvelope(BaseModel):
    data: RrspDramaDetail


class RrspDanmuItem(BaseModel):
    d: str
    p: str


# =====================
#  Scraper implementation
# =====================


class RenrenScraper(BaseScraper):
    provider_name = "renren"

    def __init__(self, pool: aiomysql.Pool, config_manager: ConfigManager):
        super().__init__(pool, config_manager)
        self.client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        self._api_lock = asyncio.Lock()
        self._last_request_time = 0.0
        self._min_interval = 0.4

    def _generate_device_id(self) -> str:
        """Generate a fresh device/session id for each request.

        RRSP services are sensitive to reusing the same deviceId for a long time.
        We follow the user's requirement to generate a new one per request.
        """
        return str(uuid.uuid4()).upper()

    async def close(self):
        await self.client.aclose()

    async def _request(self, method: str, url: str, *, params: Optional[Dict[str, Any]] = None) -> httpx.Response:
        async with self._api_lock:
            now = time.time()
            dt = now - self._last_request_time
            if dt < self._min_interval:
                await asyncio.sleep(self._min_interval - dt)
            # 每次请求使用全新的 deviceId，并按提供的签名规则构造请求头
            device_id = self._generate_device_id()
            headers = build_signed_headers(method=method, url=url, params=params or {}, device_id=device_id)
            resp = await self.client.request(method, url, params=params, headers=headers)
            if await self._should_log_responses():
                scraper_responses_logger.debug(f"Renren Response ({method} {url}): status={resp.status_code}, text={resp.text}")
            self._last_request_time = time.time()
            return resp

    async def search(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        # cache key considers keyword and optional target episode
        suffix = f"_s{episode_info['season']}e{episode_info['episode']}" if episode_info else ""
        cache_key = f"search_{self.provider_name}_{keyword}{suffix}"
        cached = await self._get_from_cache(cache_key)
        if cached is not None:
            return [models.ProviderSearchInfo.model_validate(x) for x in cached]

        url = f"{BASE_API}/m-station/search/drama"
        params = {
            "keywords": keyword,
            "size": 20,
            "order": "match",
            "search_after": "",
            "isExecuteVipActivity": True,
        }

        results: List[models.ProviderSearchInfo] = []
        try:
            resp = await self._request("GET", url, params=params)
            resp.raise_for_status()
            decoded = auto_decode(resp.text)
            data = RrspSearchResult.model_validate(decoded)
            for item in data.data.searchDramaList:
                # provider mediaId is drama id
                title_clean = re.sub(r"<[^>]+>", "", item.title).replace(":", "：")
                media_type = "tv_series"  # 人人视频以剧集为主，若将来提供电影可再细分
                episode_count = item.episode_total
                if not episode_count:
                    episode_count = await self._episode_count_from_sid(str(item.id))
                results.append(models.ProviderSearchInfo(
                    provider=self.provider_name,
                    mediaId=str(item.id),
                    title=title_clean,
                    type=media_type,
                    season=get_season_from_title(title_clean),
                    year=item.year,
                    imageUrl=item.cover,
                    episodeCount=episode_count,
                    currentEpisodeIndex=episode_info.get("episode") if episode_info else None,
                ))
        except Exception as e:
            self.logger.error(f"renren: 搜索 '{keyword}' 失败: {e}", exc_info=True)


        self.logger.info(f"renren: 搜索 '{keyword}' 完成，找到 {len(results)} 个结果。")
        if results:
            log_results = "\n".join([f"  - {r.title} (ID: {r.mediaId}, 类型: {r.type}, 年份: {r.year or 'N/A'})" for r in results])
            self.logger.info(f"renren: 搜索结果列表:\n{log_results}")

        await self._set_to_cache(cache_key, [r.model_dump() for r in results], 'search_ttl_seconds', 300)
        return results

    async def _fetch_drama_detail(self, drama_id: str) -> Optional[RrspDramaDetailEnvelope]:
        url = f"{BASE_API}/m-station/drama/page"
        params = {
            "hsdrOpen": 0,
            "isAgeLimit": 0,
            "dramaId": str(drama_id),
#            "quality": "AI4K",   #会影响获取，@didi佬
            "hevcOpen": 1,
        }
        try:
            resp = await self._request("GET", url, params=params)
            resp.raise_for_status()
            decoded = auto_decode(resp.text)
            if isinstance(decoded, dict) and 'data' in decoded:
                return RrspDramaDetailEnvelope.model_validate(decoded)
        except Exception as e:
            self.logger.error(f"renren: 获取剧集详情失败 drama_id={drama_id}: {e}", exc_info=True)
        return None

    async def _episode_count_from_sid(self, drama_id: str) -> Optional[int]:
        """Infer episode count by counting valid SID entries from drama detail.
        Args:
            drama_id: The Renren drama id.
        Returns:
            Number of episodes if episode list is available; otherwise None.
        """
        detail_env = await self._fetch_drama_detail(drama_id)
        if not detail_env or not detail_env.data or not detail_env.data.episodeList:
            return None
        return sum(1 for ep in detail_env.data.episodeList if str(ep.get("sid", "")).strip())

    async def get_episodes(self, media_id: str, target_episode_index: Optional[int] = None, db_media_type: Optional[str] = None) -> List[models.ProviderEpisodeInfo]:
        cache_key = f"episodes_{self.provider_name}_{media_id}"
        if target_episode_index is None and db_media_type is None:
            cached = await self._get_from_cache(cache_key)
            if cached is not None:
                return [models.ProviderEpisodeInfo.model_validate(e) for e in cached]

        detail_env = await self._fetch_drama_detail(media_id)
        if not detail_env or not detail_env.data or not detail_env.data.episodeList:
            return []

        # 构造分集
        episodes: List[RrspEpisodeInfo] = []
        for idx, ep in enumerate(detail_env.data.episodeList, start=1):
            sid = str(ep.get("sid", "").strip())
            if not sid:
                continue
            ep_title = str(ep.get("title") or detail_env.data.dramaInfo.title)
            episodes.append(RrspEpisodeInfo(sid=sid, order=idx, title=ep_title))

        if target_episode_index:
            episodes = [e for e in episodes if e.order == target_episode_index]

        provider_eps = [
            models.ProviderEpisodeInfo(
                provider=self.provider_name,
                episodeId=e.sid,  # 人人弹幕按 episode sid 获取
                title=e.title,
                episodeIndex=e.order,
                url=None,
            )
            for e in episodes
        ]

        # Apply custom blacklist from config
        blacklist_pattern = await self.get_episode_blacklist_pattern()
        if blacklist_pattern:
            original_count = len(provider_eps)
            provider_eps = [ep for ep in provider_eps if not blacklist_pattern.search(ep.title)]
            filtered_count = original_count - len(provider_eps)
            if filtered_count > 0:
                self.logger.info(f"Renren: 根据自定义黑名单规则过滤掉了 {filtered_count} 个分集。")

        if target_episode_index is None and db_media_type is None and provider_eps:
            await self._set_to_cache(cache_key, [e.model_dump() for e in provider_eps], 'episodes_ttl_seconds', 1800)
        return provider_eps

    async def _fetch_episode_danmu(self, sid: str) -> List[Dict[str, Any]]:
        url = f"https://static-dm.rrmj.plus/v1/produce/danmu/EPISODE/{sid}"
        try:
            # 此端点通常无需签名，但为提升成功率，带上基础头部（UA/Origin/Referer）
            prof = ClientProfile()
            headers = {
                "Accept": "application/json",
                "User-Agent": prof.user_agent,
                "Origin": prof.origin,
                "Referer": prof.referer,
            }
            resp = await self.client.get(url, timeout=20.0, headers=headers)
            if await self._should_log_responses():
                scraper_responses_logger.debug(f"Renren Danmaku Response (sid={sid}): status={resp.status_code}, text={resp.text}") 
            resp.raise_for_status()
            data = auto_decode(resp.text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                return data["data"]
        except Exception as e:
            self.logger.error(f"renren: 获取弹幕失败 sid={sid}: {e}", exc_info=True)
        return []

    def _parse_rrsp_p_fields(self, p_field: str) -> dict[str, Any]:
        parts = str(p_field).split(",")
        def _num(idx: int, cast, default):
            try:
                return cast(parts[idx])
            except Exception:
                return default
        timestamp = _num(0, float, 0.0)
        mode = _num(1, int, 1)
        size = _num(2, int, 25)
        color = _num(3, int, 16777215)
        user_id = parts[6] if len(parts) > 6 else ""
        content_id = parts[7] if len(parts) > 7 else f"{timestamp:.3f}:{user_id}"
        return {
            "timestamp": float(timestamp),
            "mode": int(mode),
            "size": int(size),
            "color": int(color),
            "user_id": str(user_id),
            "content_id": str(content_id),
        }

    def _format_comments(self, items: List[Dict[str, Any]]) -> List[dict]:
        if not items:
            return []

        # 1) 去重: 使用 content_id (p字段第7位)
        unique_map: Dict[str, Dict[str, Any]] = {}
        for it in items:
            text = str(it.get("d", ""))
            p_field = str(it.get("p", ""))
            parsed = self._parse_rrsp_p_fields(p_field)
            cid = parsed["content_id"]
            if cid not in unique_map:
                unique_map[cid] = {
                    "content": text,
                    "timestamp": parsed["timestamp"],
                    "mode": parsed["mode"],
                    "color": parsed["color"],
                    "content_id": cid,
                }

        unique_items = list(unique_map.values())

        # 2) 按内容分组，合并重复内容并在第一次出现处标注 X{n}
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for c in unique_items:
            grouped[c["content"]].append(c)

        processed: List[Dict[str, Any]] = []
        for content, group in grouped.items():
            if len(group) == 1:
                processed.append(group[0])
            else:
                first = min(group, key=lambda x: x["timestamp"])  # earliest
                first = first.copy()
                first["content"] = f"{first['content']} X{len(group)}"
                processed.append(first)

        # 3) 输出统一结构: cid, p, m, t
        out: List[dict] = []
        for c in processed:
            timestamp = float(c["timestamp"]) if isinstance(c["timestamp"], (int, float)) else 0.0
            color = int(c["color"]) if isinstance(c["color"], int) else 16777215
            mode = int(c["mode"]) if isinstance(c["mode"], int) else 1
            p_string = f"{timestamp:.2f},{mode},{color},[{self.provider_name}]"
            out.append({
                "cid": c["content_id"],
                "p": p_string,
                "m": c["content"],
                "t": timestamp,
            })
        return out

    async def get_comments(self, episode_id: str, progress_callback: Optional[Callable] = None) -> List[dict]:
        # renren uses sid as episode_id
        if progress_callback:
            await progress_callback(5, "开始获取弹幕")
        raw = await self._fetch_episode_danmu(episode_id)
        if progress_callback:
            await progress_callback(85, f"原始弹幕 {len(raw)} 条，正在规范化")
        formatted = self._format_comments(raw)
        if progress_callback:
            await progress_callback(100, f"弹幕处理完成，共 {len(formatted)} 条")
        return formatted