"""
Trakt 元数据源插件
- 搜索影视作品
- 获取作品详情（返回跨平台 ID：trakt/imdb/tmdb/tvdb）
- OAuth 授权管理（通过 CF Worker 远程代理）
- 日历日程数据
"""
import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import Request

from .base import BaseMetadataSource
from src.db import models, crud
from src.core.cache import get_cache_backend

logger = logging.getLogger(__name__)

TRAKT_API_BASE = "https://api.trakt.tv"

# 缓存配置
_CALENDAR_CACHE_TTL = 3600  # 整个日历结果缓存 1 小时
_CACHE_REGION = "metadata"
_SHOW_AIRED_TTL = 6 * 3600  # 单个 show 的 aired_episodes 缓存 6 小时
_SHOW_AIRED_KEY = "trakt_show_aired_{trakt_id}"  # 缓存 key 模板
_AIRED_FETCH_CONCURRENCY = 5  # 后台并发拉取 aired_episodes 的并发上限

# OAuth 代理地址（内置，用户无需配置）
TRAKT_OAUTH_WORKER_URL = "https://danmu-api.misaka10876.top"


class TraktMetadataSource(BaseMetadataSource):
    """Trakt.tv 元数据源 — 搜索、详情、OAuth（CF Worker 代理）、日历"""

    provider_name = "trakt"
    test_url = "https://api.trakt.tv"
    config_keys = []  # OAuth 走 CF Worker，无需用户配置 client_id/secret

    configurable_fields = {}

    # ============ 订阅助手能力 ============
    # 注意：这里的「订阅」是【弹幕库内订阅】(自动建库追更)，不是 Trakt 站内的 watchlist。
    # 流程：用户在订阅页搜番 → 命中后写 external_calendar_item(traktId/tmdbId)
    #       → IncrementalRefreshJob 自动调 auto_search_and_import_task 在弹幕库内整剧建库 + 持续追更。
    supports_subscription = True
    handled_domains = ["trakt.tv"]
    subscription_types = [
        {
            "type": "trakt_show",
            "label": "影视剧（弹幕库追更）",
            "description": "通过 Trakt 搜番，订阅后会自动在本地弹幕库建库 + 追更（不会同步到 Trakt 账号）",
            "payloadSchema": {"fields": [
                {"name": "traktId", "type": "string", "required": True, "label": "Trakt ID", "placeholder": "例如 12345"},
            ]},
        },
    ]

    async def _get_headers(self, user_token: Optional[str] = None) -> Dict[str, str]:
        """构建 Trakt API 请求头"""
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
        }
        if user_token:
            headers["Authorization"] = f"Bearer {user_token}"
        return headers

    async def _get_headers_for_user(self, user: models.User) -> Optional[Dict[str, str]]:
        """获取带有完整认证信息的 Trakt API 请求头（含 trakt-api-key）"""
        import json
        from src.core.timezone import get_now

        # 先做一次临期检测：若 access_token 即将过期（剩余 <=7 天），先经 Worker 刷新。
        # 仅当存在 refresh_token 时才会真正刷新，避免对老凭证（无 refresh_token）反复尝试。
        async with self._session_factory() as session:
            cred = await crud.get_oauth_credential_with_token(session, user.id, "trakt")
            if not cred or not cred.accessToken:
                return None
            needs_refresh = bool(
                cred.refreshToken and cred.expiresAt
                and (cred.expiresAt - get_now()).total_seconds() <= 7 * 24 * 3600
            )

        if needs_refresh:
            # 刷新失败不阻断：继续用旧 token 尝试（可能仍有效），由调用方处理 401。
            await self._refresh_token_via_worker(user)

        # 重新读取（刷新后可能已更新 accessToken）
        async with self._session_factory() as session:
            cred = await crud.get_oauth_credential_with_token(session, user.id, "trakt")
            if not cred or not cred.accessToken:
                return None
            access_token = cred.accessToken
            extra_data_raw = cred.extraData

        headers = await self._get_headers(user_token=access_token)
        # 从 extraData 中获取 client_id 作为 trakt-api-key
        if extra_data_raw:
            try:
                extra = json.loads(extra_data_raw)
                client_id = extra.get("clientId", "")
                if client_id:
                    headers["trakt-api-key"] = client_id
            except (json.JSONDecodeError, TypeError):
                pass
        return headers

    async def _refresh_token_via_worker(self, user: models.User) -> bool:
        """通过 CF Worker 的 /oauth/refresh 端点刷新 Trakt access_token。

        Trakt 的 client_secret 保存在 Worker 端，本地无法直连 trakt.tv 刷新，
        因此走 Worker 代理：POST /oauth/refresh {provider, refresh_token}
        返回 {access_token, refresh_token, expires_in, token_type, created_at}。

        Returns:
            bool: 刷新成功返回 True；无 refresh_token 或失败返回 False。
        """
        import httpx
        from datetime import timedelta
        from src.core.timezone import get_now

        # 取当前 refresh_token
        async with self._session_factory() as session:
            cred = await crud.get_oauth_credential_with_token(session, user.id, "trakt")
            if not cred or not cred.refreshToken:
                self.logger.debug("Trakt: 无 refresh_token，无法自动刷新（需重新授权）")
                return False
            refresh_token = cred.refreshToken

        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                resp = await client.post(
                    f"{TRAKT_OAUTH_WORKER_URL}/oauth/refresh",
                    json={"provider": "trakt", "refresh_token": refresh_token},
                )
                resp.raise_for_status()
                data = resp.json()

            new_access = data.get("access_token")
            if not new_access:
                self.logger.warning(f"Trakt: 刷新响应缺少 access_token: {data}")
                return False

            # refresh_token 轮换则更新，否则沿用原值
            new_refresh = data.get("refresh_token") or refresh_token
            expires_in = data.get("expires_in")

            update_data = {"accessToken": new_access, "refreshToken": new_refresh}
            try:
                if expires_in is not None:
                    update_data["expiresAt"] = get_now() + timedelta(seconds=int(expires_in))
            except (TypeError, ValueError):
                pass

            async with self._session_factory() as session:
                await crud.save_oauth_credential(session, user.id, "trakt", update_data)

            self.logger.info(f"Trakt token 已自动刷新 (用户ID: {user.id})")
            return True
        except Exception as e:
            self.logger.error(f"Trakt token 刷新失败: {e}")
            return False

    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        """搜索影视作品（需要用户已通过 OAuth 授权）"""
        import httpx
        metadata_logger = logging.getLogger('metadata_responses')
        provider_setting = await self._get_provider_setting()
        log_raw = provider_setting.get('logRawResponses', False)

        headers = await self._get_headers_for_user(user)
        if not headers or "trakt-api-key" not in headers:
            logger.warning("Trakt search: 缺少 trakt-api-key，跳过搜索")
            return []

        # 公共搜索端点 /search/show 只需 trakt-api-key(client_id)，不需要 OAuth Bearer。
        # 若带上过期/无效的 Bearer 反而触发 401，这里统一剥离 Authorization。
        public_headers = {k: v for k, v in headers.items() if k != "Authorization"}

        results = []
        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                resp = await client.get(f"{TRAKT_API_BASE}/search/show", params={"query": keyword, "extended": "full"}, headers=public_headers)
                resp.raise_for_status()
                raw_text = resp.text

                if log_raw:
                    metadata_logger.info(
                        f"Trakt Search Response for '{keyword}': URL={resp.url} | Status={resp.status_code} | Body={raw_text}"
                    )

                for item in resp.json()[:20]:
                    show = item.get("show", {})
                    ids = show.get("ids", {})
                    results.append(models.MetadataDetailsResponse(
                        id=str(ids.get("trakt", "")),
                        title=show.get("title", ""),
                        year=show.get("year"),
                        type="tv_series",
                        details=show.get("overview", ""),
                        imageUrl=None,
                        tmdbId=str(ids.get("tmdb", "")) if ids.get("tmdb") else None,
                        imdbId=ids.get("imdb"),
                        tvdbId=str(ids.get("tvdb", "")) if ids.get("tvdb") else None,
                        provider="trakt",
                    ))
        except Exception as e:
            logger.error(f"Trakt search error: {e}")
        return results

    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        """获取作品详情（需要用户已通过 OAuth 授权）"""
        import httpx
        metadata_logger = logging.getLogger('metadata_responses')
        provider_setting = await self._get_provider_setting()
        log_raw = provider_setting.get('logRawResponses', False)

        headers = await self._get_headers_for_user(user)
        if not headers:
            logger.warning("Trakt get_details: 用户未授权，跳过")
            return None

        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                resp = await client.get(f"{TRAKT_API_BASE}/shows/{item_id}", params={"extended": "full"}, headers=headers)
                resp.raise_for_status()
                raw_text = resp.text

                if log_raw:
                    metadata_logger.info(
                        f"Trakt Detail Response for '{item_id}': URL={resp.url} | Status={resp.status_code} | Body={raw_text}"
                    )

                show = resp.json()
                ids = show.get("ids", {})
                return models.MetadataDetailsResponse(
                    id=str(ids.get("trakt", "")),
                    title=show.get("title", ""),
                    year=show.get("year"),
                    type="tv_series",
                    details=show.get("overview", ""),
                    imageUrl=None,
                    tmdbId=str(ids.get("tmdb", "")) if ids.get("tmdb") else None,
                    imdbId=ids.get("imdb"),
                    tvdbId=str(ids.get("tvdb", "")) if ids.get("tvdb") else None,
                    provider="trakt",
                )
        except Exception as e:
            logger.error(f"Trakt get_details error: {e}")
        return None

    async def search_aliases(self, keyword: str, user: models.User) -> Set[str]:
        """Trakt 不直接提供别名，返回空"""
        return set()

    async def get_calendar(self, user: models.User) -> List[Dict[str, Any]]:
        """从 Trakt /calendars/all/shows 获取公共日历（所有在播番剧）"""
        import httpx
        from datetime import datetime

        metadata_logger = logging.getLogger('metadata_responses')
        provider_setting = await self._get_provider_setting()
        log_raw = provider_setting.get('logRawResponses', False)

        today = datetime.now().strftime("%Y-%m-%d")
        cache_key = f"trakt_calendar_{today}"

        # 优先读取整个日历结果缓存（已含图片）
        cache_backend = get_cache_backend()
        try:
            cached = await cache_backend.get(cache_key, region=_CACHE_REGION)
            if cached is not None:
                return cached
        except Exception:
            pass

        # 公共日历只需要 trakt-api-key（client_id），不强制要求 OAuth
        headers = await self._get_headers_for_user(user)
        if not headers or "trakt-api-key" not in headers:
            self.logger.debug("Trakt get_calendar: 缺少 trakt-api-key，跳过")
            return []

        # 公共日历不需要 OAuth Bearer，移除以避免 rate limit 差异
        public_headers = {k: v for k, v in headers.items() if k != "Authorization"}

        items = []
        url = f"{TRAKT_API_BASE}/calendars/all/shows/{today}/7"
        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                resp = await client.get(url, headers=public_headers)
                resp.raise_for_status()
                raw_text = resp.text

                if log_raw:
                    metadata_logger.info(
                        f"Trakt Calendar Response: URL={resp.url} | Status={resp.status_code} | Body={raw_text}"
                    )

                trakt_calendar = resp.json()

            seen_trakt_ids = set()
            for entry in trakt_calendar:
                show = entry.get("show", {})
                ids = show.get("ids", {})
                trakt_id = str(ids.get("trakt", ""))
                # 去重：Trakt 日历同一剧的多集会返回多条 entry，仅保留首条
                if trakt_id and trakt_id in seen_trakt_ids:
                    continue
                first_aired = entry.get("first_aired", "")
                weekday = None
                if first_aired:
                    try:
                        air_dt = datetime.fromisoformat(first_aired.replace("Z", "+00:00"))
                        weekday = air_dt.isoweekday()
                    except (ValueError, TypeError):
                        pass
                if not weekday:
                    continue
                if trakt_id:
                    seen_trakt_ids.add(trakt_id)
                episode = entry.get("episode", {}) or {}
                # 当前集号（追更进度）：直接用 entry 里当天播出的 episode.number
                current_ep = episode.get("number")
                # 总集数：尝试读缓存（由后台任务补全），未命中则留 None → 前端显示 ∞
                aired_total = None
                if trakt_id:
                    try:
                        aired_total = await cache_backend.get(
                            _SHOW_AIRED_KEY.format(trakt_id=trakt_id), region=_CACHE_REGION
                        )
                    except Exception:
                        aired_total = None
                items.append({
                    "animeTitle": show.get("title", ""),
                    "airWeekday": weekday,
                    "origin": "trakt",
                    "isLocal": False,
                    "bangumiId": None,
                    "traktId": trakt_id,
                    "imageUrl": None,
                    "traktImdbId": ids.get("imdb"),
                    "traktTmdbId": ids.get("tmdb"),
                    "year": show.get("year"),
                    "season": episode.get("season"),
                    "airDate": first_aired[:10] if first_aired else None,
                    "latestEpisodeIndex": current_ep,
                    "episodeCount": aired_total,  # 总集数（缓存命中才有，否则 None → 前端 ∞）
                })
        except Exception as e:
            self.logger.warning(f"Trakt 日历获取失败: {e}")
            return items

        # 注意：不再同步等待 TMDB 海报（避免 237 个请求阻塞日历响应）。
        # 海报改由前端 <img> 按需请求 /ui/calendar/tmdb-poster/{tmdb_id} 端点懒加载。
        # 仅缓存轻量日历结构（含 traktTmdbId），减少对 Trakt 的重复请求。
        try:
            await cache_backend.set(cache_key, items, ttl=_CALENDAR_CACHE_TTL, region=_CACHE_REGION)
        except Exception:
            pass

        # 后台慢拉总集数（aired_episodes）：仅对未命中缓存的 trakt_id 触发，写入个例缓存
        # 不阻塞当前响应；下一次日历刷新时会从缓存读到 episodeCount → 进度条有真实填充
        missing_ids = [it.get("traktId") for it in items
                       if it.get("traktId") and it.get("episodeCount") is None]
        if missing_ids:
            import asyncio
            asyncio.create_task(self._fetch_aired_episodes_background(missing_ids, public_headers))

        return items

    async def get_user_watching_collection(self, user: models.User) -> Dict[str, Dict[str, Any]]:
        """拉取「Trakt 账号下我的在追」列表 — 用于补充 external_calendar_item.platformWatchStatus。

        Trakt 没有像 BGM 那样统一的 collection type 端点，需要分多个端点查：
            GET /users/me/watched/shows  -> 看过/在看（按 last_watched_at 排序）
            GET /users/me/watchlist/shows -> 想看 (wish)

        本方法把 watched 列表全部标记为 'watching'（Trakt 没有「完成/在看」二态区分，
        只要看过任何一集就在 watched 里）。watchlist 标记为 'wish'。

        :return: { trakt_id: {'status': 'watching'|'wish', 'watchedEps': int|None, 'rating': float|None} }
                  未授权时返回 {}
        """
        import httpx

        headers = await self._get_headers_for_user(user)
        if not headers:
            self.logger.debug("Trakt: 未授权，跳过 get_user_watching_collection")
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=20, proxy=proxy) as client:
                # 1) 已看（含「在看」）— /users/me/watched/shows 返回所有看过任意集的 show
                try:
                    resp = await client.get(
                        f"{TRAKT_API_BASE}/users/me/watched/shows",
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        watched_data = resp.json() or []
                        for entry in watched_data:
                            show = entry.get("show") or {}
                            ids = show.get("ids") or {}
                            trakt_id = ids.get("trakt")
                            if not trakt_id:
                                continue
                            # plays = 总观看次数；aired_episodes 字段在该接口下不可用
                            # 用 seasons → episodes 数量计算「看到第几集」（Trakt 无线性集号，取累计观看集数）
                            seasons = entry.get("seasons") or []
                            watched_eps = sum(
                                len(s.get("episodes") or []) for s in seasons
                            ) or None
                            result[str(trakt_id)] = {
                                "status": "watching",
                                "watchedEps": watched_eps,
                                "rating": None,  # watched 接口不带 rating，下面 ratings 接口补
                            }
                    else:
                        self.logger.warning(
                            f"Trakt watched/shows 失败 status={resp.status_code} body={resp.text[:200]}"
                        )
                except Exception as e:
                    self.logger.warning(f"Trakt watched/shows 异常: {e}")

                # 2) 想看 — /users/me/watchlist/shows
                try:
                    resp = await client.get(
                        f"{TRAKT_API_BASE}/users/me/watchlist/shows",
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        wl_data = resp.json() or []
                        for entry in wl_data:
                            show = entry.get("show") or {}
                            ids = show.get("ids") or {}
                            trakt_id = ids.get("trakt")
                            if not trakt_id:
                                continue
                            sid = str(trakt_id)
                            # 已经在 watched 里的优先保留 'watching'，不被 wish 覆盖
                            if sid not in result:
                                result[sid] = {
                                    "status": "wish",
                                    "watchedEps": None,
                                    "rating": None,
                                }
                except Exception as e:
                    self.logger.warning(f"Trakt watchlist/shows 异常: {e}")

                # 3) 用户评分 — /users/me/ratings/shows
                try:
                    resp = await client.get(
                        f"{TRAKT_API_BASE}/users/me/ratings/shows",
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        ratings_data = resp.json() or []
                        for entry in ratings_data:
                            show = entry.get("show") or {}
                            ids = show.get("ids") or {}
                            trakt_id = ids.get("trakt")
                            rating = entry.get("rating")
                            if trakt_id and rating and str(trakt_id) in result:
                                result[str(trakt_id)]["rating"] = float(rating)
                except Exception as e:
                    self.logger.debug(f"Trakt ratings/shows 异常（忽略）: {e}")
        except Exception as e:
            self.logger.warning(f"Trakt get_user_watching_collection 整体异常: {e}")
            return {}

        self.logger.info(f"Trakt 在追列表: 共 {len(result)} 条")
        return result



    async def _fetch_aired_episodes_background(
        self, trakt_ids: List[str], public_headers: Dict[str, str]
    ) -> None:
        """后台并发拉取每个 show 的 aired_episodes，写入缓存（TTL 6h）。

        - 不抛异常（后台任务，安静失败）
        - 并发受 _AIRED_FETCH_CONCURRENCY 限制，避免触发 Trakt 限流（公开 API 1000/5min）
        - 仅写个例缓存；下一次 get_calendar 调用会自动读到 episodeCount
        """
        import asyncio
        import httpx

        cache_backend = get_cache_backend()
        sem = asyncio.Semaphore(_AIRED_FETCH_CONCURRENCY)

        async def _one(client: httpx.AsyncClient, trakt_id: str) -> None:
            async with sem:
                try:
                    resp = await client.get(
                        f"{TRAKT_API_BASE}/shows/{trakt_id}",
                        params={"extended": "full"},
                        headers=public_headers,
                    )
                    if resp.status_code != 200:
                        return
                    data = resp.json() or {}
                    aired = data.get("aired_episodes")
                    if isinstance(aired, int) and aired > 0:
                        try:
                            await cache_backend.set(
                                _SHOW_AIRED_KEY.format(trakt_id=trakt_id),
                                aired,
                                ttl=_SHOW_AIRED_TTL,
                                region=_CACHE_REGION,
                            )
                        except Exception:
                            pass
                except Exception:
                    # 单个失败不影响其他
                    pass

        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                await asyncio.gather(*(_one(client, tid) for tid in trakt_ids), return_exceptions=True)
            self.logger.debug(f"Trakt 后台拉取 aired_episodes 完成: {len(trakt_ids)} 个 show")
        except Exception as e:
            self.logger.debug(f"Trakt 后台拉取 aired_episodes 失败: {e}")

    async def _get_provider_setting(self) -> Dict[str, Any]:
        """获取当前源的设置"""
        async with self._session_factory() as session:
            settings = await crud.get_all_metadata_source_settings(session)
            return next((s for s in settings if s['providerName'] == self.provider_name), {})

    async def _get_proxy(self) -> Optional[str]:
        """当 trakt 源开启 useProxy 时，返回代理 URL。"""
        proxy_mode = await self.config_manager.get("proxyMode", "none")
        if proxy_mode == "none":
            if (await self.config_manager.get("proxyEnabled", "false")).lower() == "true":
                proxy_mode = "http_socks"
        if proxy_mode != "http_socks":
            return None
        proxy_url = await self.config_manager.get("proxyUrl", "")
        if not proxy_url:
            return None
        provider_setting = await self._get_provider_setting()
        return proxy_url if provider_setting.get("useProxy", False) else None

    async def check_connectivity(self) -> Dict[str, str]:
        """检查 Trakt 连通性 — 通过 CF Worker 的 OAuth providers 端点"""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{TRAKT_OAUTH_WORKER_URL}/oauth/providers")
                resp.raise_for_status()
                data = resp.json()
                if "trakt" in data.get("providers", []):
                    return {"code": "ok", "message": "Trakt OAuth 服务可用"}
                return {"code": "error", "message": "CF Worker 未配置 Trakt Provider"}
        except Exception as e:
            return {"code": "error", "message": f"CF Worker 连接失败: {e}"}

    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: models.User, request: Request) -> Any:
        """处理 OAuth 授权相关操作"""
        if action_name == "get_auth_status":
            return await self._get_auth_status(user)
        elif action_name == "save_oauth":
            return await self._save_oauth(user, payload)
        elif action_name == "revoke_auth":
            return await self._revoke_auth(user)
        raise NotImplementedError(f"未知操作: {action_name}")

    async def _get_auth_status(self, user: models.User) -> Dict[str, Any]:
        async with self._session_factory() as session:
            return await crud.get_oauth_credential(session, user.id, "trakt")

    async def _save_oauth(self, user: models.User, payload: Dict) -> Dict[str, Any]:
        """保存从 CF Worker OAuth 回调获得的 token 信息"""
        access_token = payload.get("accessToken")
        if not access_token:
            return {"success": False, "message": "缺少 access_token"}

        import json
        from datetime import timedelta
        from src.core.timezone import get_now

        # 将 client_id 存入 extraData（JSON 格式），用于后续 API 调用时带 trakt-api-key
        extra_data = {}
        client_id = payload.get("clientId", "")
        if client_id:
            extra_data["clientId"] = client_id

        # 落库 refresh_token 与过期时间，用于后续自动刷新（修复：此前从未保存，导致 token 过期后无法刷新）。
        # 兼容前端可能传 refreshToken / refresh_token 两种命名；expiresIn 为秒。
        refresh_token = payload.get("refreshToken") or payload.get("refresh_token") or None
        expires_in = payload.get("expiresIn") or payload.get("expires_in")
        save_data = {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "providerUserId": payload.get("userId", ""),
            "providerUsername": payload.get("username", ""),
            "authorizedAt": get_now(),
            "extraData": json.dumps(extra_data) if extra_data else None,
        }
        # 仅当拿到有效 expires_in 时才写过期时间，避免把 None 误当作"立即过期"。
        try:
            if expires_in is not None:
                save_data["expiresAt"] = get_now() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            pass

        async with self._session_factory() as session:
            await crud.save_oauth_credential(session, user.id, "trakt", save_data)
        return {"success": True, "message": "Trakt 授权成功"}

    async def _revoke_auth(self, user: models.User) -> Dict[str, Any]:
        async with self._session_factory() as session:
            deleted = await crud.delete_oauth_credential(session, user.id, "trakt")
        return {"success": deleted, "message": "已撤销 Trakt 授权" if deleted else "未找到授权信息"}

    # ============ 订阅助手实现 ============

    async def check_subscription_capability(self, user=None) -> Dict[str, Any]:
        """Trakt 订阅可用性：用户已 OAuth 授权且 client_id 完整即可用。"""
        if user is None:
            # /available-sources 阶段允许 user 透传；不强求授权也声明可见，但 available=False 引导用户授权
            return {
                "available": False, "authRequired": True, "authStatus": "unknown",
                "reason": "需登录后检测授权状态", "subscriptionTypes": self.subscription_types,
            }
        headers = await self._get_headers_for_user(user)
        if not headers or "trakt-api-key" not in headers:
            return {
                "available": False, "authRequired": True, "authStatus": "missing",
                "reason": "请先完成 Trakt OAuth 授权", "subscriptionTypes": self.subscription_types,
            }
        return {
            "available": True, "authRequired": True, "authStatus": "valid",
            "reason": None, "subscriptionTypes": self.subscription_types,
        }

    async def discover_subscription_targets(self, query: str, subscription_type: str = "", user=None) -> List[Dict[str, Any]]:
        """搜 Trakt 影视剧候选；复用现有 search()。"""
        query = (query or "").strip()
        if not query or user is None:
            return []
        try:
            results = await self.search(query, user=user)
        except Exception as e:
            self.logger.warning(f"Trakt: 订阅 discover 搜索失败: {type(e).__name__}: {e}")
            return []
        out = []
        for r in results[:20]:
            if not r.id:
                continue
            desc = (r.details or "")[:80]
            year = f" · {r.year}" if r.year else ""
            out.append({
                "type": "trakt_show",
                "title": r.title or f"Trakt {r.id}",
                "cover": r.imageUrl,
                "description": f"Trakt ID {r.id}{year}" + (f" · {desc}" if desc else ""),
                "payload": {
                    "traktId": str(r.id),
                    "tmdbId": str(r.tmdbId) if r.tmdbId else None,
                    "title": r.title or "",
                    "year": r.year,
                    "imageUrl": r.imageUrl,
                },
            })
        return out

    async def validate_subscription_payload(self, subscription_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """校验 Trakt 订阅 payload，统一返回标准结构（写入 external_calendar_item）。"""
        if subscription_type != "trakt_show":
            raise ValueError(f"Trakt 暂不支持订阅类型: {subscription_type}")
        trakt_id = (payload or {}).get("traktId") or ""
        if not str(trakt_id).strip():
            raise ValueError("缺少 traktId")
        title = (payload or {}).get("title") or ""
        tmdb_id = (payload or {}).get("tmdbId")
        return {
            "provider": self.provider_name,
            "externalId": f"trakt-{trakt_id}",
            "title": title,
            "animeType": "tv_series",
            "subscriptionType": "trakt_show",
            "extraData": {
                "traktId": str(trakt_id),
                "tmdbId": str(tmdb_id) if tmdb_id else None,
                "year": (payload or {}).get("year"),
                "imageUrl": (payload or {}).get("imageUrl"),
            },
        }
