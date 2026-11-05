import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

import httpx
from fastapi import HTTPException, status

from src.schemas import MetadataDetailsResponse, User
from src.services.service_container import get_database_service
from .base import BaseMetadataSource, HTTPStatusError

logger = logging.getLogger(__name__)

class TvdbMetadataSource(BaseMetadataSource):
    provider_name = "tvdb"
    config_keys = ["tvdbApiKey"]
    test_url = "https://api4.thetvdb.com"

    # ============ 订阅助手能力 ============
    # 订阅页搜 TVDB 剧集 → 写订阅目标 → IncrementalRefreshJob 按 TVDB ID 自动建库追更
    supports_subscription = True
    handled_domains = ["thetvdb.com"]
    subscription_types = [
        {
            "type": "tvdb_series",
            "label": "剧集（弹幕库追更）",
            "description": "通过 TVDB 搜剧集，订阅后按 TVDB ID 自动在本地弹幕库建库并追更",
            "payloadSchema": {"fields": [
                {"name": "tvdbId", "type": "string", "required": True, "label": "TVDB ID", "placeholder": "例如 81189"},
                {"name": "season", "type": "number", "required": False, "label": "季号", "placeholder": "默认 1"},
            ]},
        },
    ]

    async def _get_tvdb_token(self, client: httpx.AsyncClient) -> str:
        """获取一个有效的TVDB令牌，如果需要则从数据库或API刷新。

        参考Bangumi源的实现：主动监测token有效性，当剩余天数<=7天时提前刷新。
        """
        # 1. 尝试从数据库配置中获取缓存的token和过期时间
        token = await self.config_service.get("tvdbJwtToken")
        expires_at_str = await self.config_service.get("tvdbTokenExpiresAt")

        if token and expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str)
                now = datetime.utcnow()
                days_left = (expires_at - now).days

                if days_left > 7:
                    # 剩余超过7天，直接使用缓存的token
                    self.logger.debug(f"TVDB: 使用缓存的有效token (剩余 {days_left} 天)。")
                    return token
                elif days_left > 0:
                    # 剩余1-7天，提前刷新
                    self.logger.info(f"TVDB token 剩余 {days_left} 天，提前刷新以避免过期...")
                else:
                    # 已过期
                    self.logger.info("TVDB token 已过期，正在刷新...")
            except ValueError:
                self.logger.warning("TVDB: 数据库中的过期时间格式无效，将重新获取token。")
        else:
            self.logger.info("TVDB token 未找到，正在请求新的令牌。")
        api_key = await self.config_service.get("tvdbApiKey", "")
        if not api_key:
            raise ValueError("TVDB API Key 未配置。")

        try:
            # 登录端点不需要认证头
            login_response = await client.post("/login", json={"apikey": api_key})
            login_response.raise_for_status()
            new_token = login_response.json().get("data", {}).get("token")
            if not new_token:
                raise ValueError("登录响应中未包含令牌。")

            # 令牌有效期为一个月，我们设置一个29天的缓存
            new_expires_at = datetime.utcnow() + timedelta(days=29)
            await self.config_service.set("tvdbJwtToken", new_token)
            await self.config_service.set("tvdbTokenExpiresAt", new_expires_at.isoformat())
            
            self.logger.info("成功获取并缓存新的TVDB令牌。")
            return new_token
        except Exception as e:
            self.logger.error(f"获取TVDB令牌失败: {e}", exc_info=True)
            raise ValueError("TVDB认证失败。")

    async def _create_client(self) -> httpx.AsyncClient:
        # 1. 获取代理配置
        # 已迁移：配置读取统一走 config_service，与其他元数据源保持一致
        proxy_url = await self.config_service.get("proxyUrl", "")
        proxy_enabled_str = await self.config_service.get("proxyEnabled", "false")
        proxy_enabled_globally = proxy_enabled_str.lower() == 'true'
        db = get_database_service()
        async with db.transaction():
            metadata_settings = await db.metadata_source.get_all_metadata_source_settings()
        provider_setting = next((s for s in metadata_settings if s['providerName'] == self.provider_name), None)
        use_proxy_for_this_provider = provider_setting.get('useProxy', False) if provider_setting else False

        proxy_to_use = proxy_url if proxy_enabled_globally and use_proxy_for_this_provider and proxy_url else None

        # 2. 创建一个基础客户端用于登录
        base_client = httpx.AsyncClient(base_url="https://api4.thetvdb.com/v4", timeout=20.0, follow_redirects=True, proxy=proxy_to_use)

        # 3. 使用基础客户端获取认证Token
        token = await self._get_tvdb_token(base_client)

        # 4. 将认证Token更新到客户端的请求头中
        base_client.headers.update({
            "Authorization": f"Bearer {token}",
            "User-Agent": "DanmuApiServer/1.0"
        })
        return base_client

    async def search(self, keyword: str, user: User, mediaType: Optional[str] = None) -> List[MetadataDetailsResponse]:
        try:
            async with await self._create_client() as client:
                params = {"query": keyword}
                if mediaType:
                    params["type"] = mediaType

                response = await client.get("/search", params=params)
                response.raise_for_status()
                data = response.json().get("data", [])

                results = []
                for item in data:
                    results.append(MetadataDetailsResponse(
                        id=item['tvdb_id'], tvdbId=item['tvdb_id'],
                        title=item.get('name'), imageUrl=item.get('image_url'),
                        details=f"Year: {item.get('year')}",
                        type="tv_series" if item.get("type") == "series" else item.get("type", "other")
                    ))
                return results
        except ValueError as e:
            self.logger.error(f"TVDB搜索失败，配置错误: {e}")
            return []
        except httpx.ConnectError as e:
            self.logger.warning(f"TVDB搜索失败，连接错误（可能是网络问题或服务不可用）: {e}")
            return []
        except httpx.TimeoutException as e:
            self.logger.warning(f"TVDB搜索失败，请求超时: {e}")
            return []
        except httpx.HTTPStatusError as e:
            self.logger.error(f"TVDB搜索失败，HTTP错误: {e.response.status_code} for URL: {e.request.url}")
            return []
        except Exception as e:
            self.logger.warning(f"TVDB搜索失败，发生意外错误: {e}")
            return []

    async def get_details(self, item_id: str, user: User, mediaType: Optional[str] = None) -> Optional[MetadataDetailsResponse]:
        try:
            async with await self._create_client() as client:
                async def _fetch_and_parse(entity_type: str) -> Optional[MetadataDetailsResponse]:
                    try:
                        self.logger.info(f"TVDB: 正在尝试将ID {item_id} 作为 '{entity_type}' 获取...")
                        response = await client.get(f"/{entity_type}/{item_id}/extended")
                        if response.status_code == 404:
                            self.logger.debug(f"TVDB: ID {item_id} 未被找到为 '{entity_type}'。")
                            return None
                        response.raise_for_status()
                        details = response.json().get("data", {})
                        if not details: return None
                        imdb_id = None
                        if remote_ids := details.get('remoteIds'):
                            imdb_entry = next((rid for rid in remote_ids if rid.get('sourceName') == 'IMDB'), None)
                            if imdb_entry: imdb_id = imdb_entry.get('id')
                        return MetadataDetailsResponse(
                            id=str(details['id']), tvdbId=str(details['id']), title=details.get('name'),
                            imageUrl=details.get('image'), details=details.get('overview'), imdbId=imdb_id,
                            type='movie' if entity_type == 'movies' else 'tv_series',
                            year=int(details['year']) if details.get('year') and details['year'].isdigit() else None
                        )
                    except httpx.HTTPStatusError as e:
                        self.logger.error(f"TVDB: 获取 {entity_type} (ID: {item_id}) 时发生HTTP错误: {e.response.status_code}")
                        return None
                    except Exception as e:
                        self.logger.error(f"TVDB: 处理 {entity_type} (ID: {item_id}) 时发生未知错误: {e}", exc_info=True)
                        return None

                details = None
                if mediaType == "movie":
                    details = await _fetch_and_parse("movies")
                elif mediaType == "series":
                    details = await _fetch_and_parse("series")
                else:
                    details = await _fetch_and_parse("movies")
                    if not details:
                        self.logger.info(f"TVDB: 作为电影获取失败，正在尝试作为电视剧获取...")
                        details = await _fetch_and_parse("series")
                return details
        except ValueError as e:
            self.logger.error(f"TVDB获取详情失败 (item_id={item_id}): {e}")
            raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(e))
        except Exception as e:
            self.logger.error(f"TVDB获取详情失败: {e}", exc_info=True)
            return None

    async def search_aliases(self, keyword: str, user: User) -> Set[str]:
        return set()

    async def check_connectivity(self) -> Dict[str, str]:
        """检查TVDB源配置状态"""
        try:
            api_key = await self.config_service.get("tvdbApiKey", "")
            if not api_key or api_key.strip() == "":
                return {"code": "unconfigured", "message": "未配置 (缺少TVDB API Key)"}

            # 检查API Key格式是否合理
            if len(api_key.strip()) < 10:
                return {"code": "error", "message": "配置异常 (API Key格式不正确)"}

            return {"code": "ok", "message": "配置正常"}
        except Exception as e:
            return {"code": "error", "message": f"配置检查失败: {e}"}
    async def execute_action(self, action_name: str, payload: Dict, user: User) -> Any:
        """TVDB source does not support custom actions."""
        raise NotImplementedError(f"源 '{self.provider_name}' 不支持任何自定义操作。")

    # ============ 订阅助手实现 ============
    # 订阅即【弹幕库内订阅】：命中后写 external_calendar_item(extraData.tvdbId)，
    # 由 IncrementalRefreshJob 按 TVDB ID 走 auto_import 精确建库 + 追更。

    async def check_subscription_capability(self, user=None) -> Dict[str, Any]:
        """TVDB 订阅可用性：配置了 API Key 即可用。"""
        api_key = await self.config_service.get("tvdbApiKey")
        if not api_key:
            return {
                "available": False, "authRequired": False, "authStatus": "missing",
                "reason": "请先配置 TVDB API Key", "subscriptionTypes": self.subscription_types,
            }
        return {
            "available": True, "authRequired": False, "authStatus": "none",
            "reason": None, "subscriptionTypes": self.subscription_types,
        }

    async def discover_subscription_targets(self, query: str, subscription_type: str = "", user=None) -> List[Dict[str, Any]]:
        """搜 TVDB 剧集候选；复用现有 search()。"""
        query = (query or "").strip()
        if not query:
            return []
        search_user = user or User(id=0, username="subscription")
        results = await self.search(query, user=search_user, mediaType="series")
        out: List[Dict[str, Any]] = []
        for r in results[:20]:
            if not r.id:
                continue
            out.append({
                "type": "tvdb_series",
                "title": r.title or f"TVDB {r.id}",
                "cover": r.imageUrl,
                "description": f"TVDB 剧集 {r.id}" + (f" · {r.details}" if r.details else ""),
                "payload": {
                    "tvdbId": str(r.id),
                    "title": r.title or "",
                    "imageUrl": r.imageUrl,
                },
            })
        return out

    async def validate_subscription_payload(self, subscription_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """校验 TVDB 订阅 payload；可带季号（默认第 1 季）。"""
        if subscription_type != "tvdb_series":
            raise ValueError(f"TVDB 暂不支持订阅类型: {subscription_type}")
        payload = payload or {}
        tvdb_id = str(payload.get("tvdbId") or "").strip()
        if not tvdb_id.isdigit():
            raise ValueError("缺少有效的 tvdbId")
        try:
            season = int(payload.get("season") or 1)
        except (TypeError, ValueError):
            raise ValueError("季号必须是数字")
        return {
            "provider": self.provider_name,
            "externalId": f"tvdb-{tvdb_id}-s{season}",
            "title": payload.get("title") or "",
            "animeType": "tv_series",
            "subscriptionType": "tvdb_series",
            "extraData": {
                "tvdbId": tvdb_id,
                "season": season,
                "imageUrl": payload.get("imageUrl"),
            },
        }
