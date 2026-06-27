import logging
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import crud, models, orm_models, get_db_session, ConfigManager, CacheManager
from src.core import get_app_timezone, get_now
from src.security import get_current_user
from src.core import settings
from src.core.cache import get_cache_backend
from src.utils import parse_search_keyword
from src.utils import clean_movie_title as _clean_movie_title
from src.services import ScraperManager
from .base import BaseMetadataSource

logger = logging.getLogger(__name__)
metadata_logger = logging.getLogger("scraper_responses")


class InfoboxItem(BaseModel):
    key: str
    value: Any

class BangumiSearchSubject(BaseModel):
    id: int
    name: str
    name_cn: str
    images: Optional[Dict[str, str]] = None
    date: Optional[str] = None
    infobox: Optional[List[InfoboxItem]] = None

    @model_validator(mode='after')
    def clean_titles(self) -> 'BangumiSearchSubject':
        self.name = _clean_movie_title(self.name)
        self.name_cn = _clean_movie_title(self.name_cn)
        return self

    @property
    def display_name(self) -> str:
        return self.name_cn or self.name

    @property
    def image_url(self) -> Optional[str]:
        if self.images:
            for size in ["large", "common", "medium", "small", "grid"]:
                if url := self.images.get(size):
                    return url
        return None

    @property
    def aliases(self) -> Dict[str, Any]:
        data = {"name_en": None, "name_romaji": None, "aliases_cn": []}
        if not self.infobox: return data

        def extract_value(value: Any) -> List[str]:
            if isinstance(value, str): return [v.strip() for v in value.split('/') if v.strip()]
            elif isinstance(value, list): return [v.get("v", "").strip() for v in value if isinstance(v, dict) and v.get("v")]
            return []

        all_raw_aliases = []
        for item in self.infobox:
            key, value = item.key.strip(), item.value
            if key == "英文名" and isinstance(value, str): data["name_en"] = _clean_movie_title(value.strip())
            elif key == "罗马字" and isinstance(value, str): data["name_romaji"] = _clean_movie_title(value.strip())
            elif key == "别名": all_raw_aliases.extend(extract_value(value))

        chinese_char_pattern = re.compile(r'[\u4e00-\u9fa5]')
        cleaned_aliases = [_clean_movie_title(alias) for alias in all_raw_aliases]
        data["aliases_cn"] = [alias for alias in cleaned_aliases if alias and chinese_char_pattern.search(alias)]
        data["aliases_cn"] = list(dict.fromkeys(data["aliases_cn"]))
        return data

    @property
    def details_string(self) -> str:
        parts = []
        if self.date:
            try: parts.append(date.fromisoformat(self.date).strftime('%Y年%m月%d日'))
            except (ValueError, TypeError): parts.append(self.date)

        if self.infobox:
            staff_keys = ["导演", "原作", "脚本", "人物设定", "系列构成", "总作画监督"]
            staff_found = {}
            for item in self.infobox:
                if item.key in staff_keys:
                    value_str = ""
                    if isinstance(item.value, str): value_str = item.value.strip()
                    elif isinstance(item.value, list): value_str = "、".join([v.get("v", "").strip() for v in item.value if isinstance(v, dict) and v.get("v")])
                    if value_str: staff_found[item.key] = value_str
            for key in staff_keys:
                if key in staff_found and len(parts) < 5: parts.append(staff_found[key])
        return " / ".join(parts)

class BangumiSearchResponse(BaseModel):
    data: Optional[List[BangumiSearchSubject]] = None


# ====================================================================
# NEW: Bangumi Auth DB Helpers (kept within this module)
# ====================================================================

async def _get_bangumi_auth(session: AsyncSession, user_id: int) -> Dict[str, Any]:
    """获取用户的Bangumi授权状态。"""
    auth = await session.get(orm_models.BangumiAuth, user_id)
    if not auth:
        return {"isAuthenticated": False}

    # 修正：由于所有时间都以 naive UTC-like 形式存储，直接与当前的 naive UTC-like 时间比较
    now = get_now()
    if auth.expiresAt and auth.expiresAt < now:
        return {"isAuthenticated": False, "isExpired": True}

    # 计算剩余天数
    days_left = 0
    if auth.expiresAt:
        time_diff = auth.expiresAt - now
        days_left = time_diff.days

    return {
        "isAuthenticated": True, "bangumiUserId": auth.bangumiUserId,
        "nickname": auth.nickname, "username": auth.username,
        "sign": auth.sign, "avatarUrl": auth.avatarUrl,
        "authorizedAt": auth.authorizedAt, "expiresAt": auth.expiresAt,
        "accessToken": auth.accessToken, "daysLeft": days_left
    }

async def _save_bangumi_auth(session: AsyncSession, user_id: int, auth_data: Dict[str, Any]):
    """保存或更新用户的Bangumi授权信息。"""
    existing_auth = await session.get(orm_models.BangumiAuth, user_id)

    if existing_auth:
        for key, value in auth_data.items():
            setattr(existing_auth, key, value)
        existing_auth.authorizedAt = get_now()
    else:
        new_auth = orm_models.BangumiAuth(userId=user_id, **auth_data, authorizedAt=get_now())
        session.add(new_auth)
    await session.flush()

async def _delete_bangumi_auth(session: AsyncSession, user_id: int):
    """删除用户的Bangumi授权信息。"""
    stmt = delete(orm_models.BangumiAuth).where(orm_models.BangumiAuth.userId == user_id)
    await session.execute(stmt)

async def _get_bgm_token_status(access_token: str, proxy: Optional[str] = None) -> Optional[int]:
    """实时查询 bgm.tv 上某个 access_token 的剩余有效秒数。

    参考 ani-rss：不信任本地 expiresAt（可能因时区/未存 expires_in 而算错），
    直接调 bgm.tv 的 token_status 接口拿真实过期时间。

    Returns:
        - 剩余秒数（>0 有效，<=0 已过期）
        - None：查询失败（网络错误/接口异常），调用方应回退到本地 expiresAt 判断
    """
    if not access_token:
        return None
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=15.0) as client:
            # bgm.tv 官方：POST /oauth/token_status，form 传 access_token，返回 expires(秒级时间戳)
            resp = await client.post(
                "https://bgm.tv/oauth/token_status",
                data={"access_token": access_token},
            )
            if resp.status_code != 200:
                # 401/400 通常表示 token 已失效
                logger.debug(f"Bangumi token_status 返回非 200: {resp.status_code}")
                return None
            data = resp.json()
            expires_ts = data.get("expires")
            if expires_ts is None:
                return None
            # expires 是 UTC 秒级时间戳；与当前 UTC 时间比较得到剩余秒数
            import time
            return int(expires_ts) - int(time.time())
    except Exception as e:
        logger.debug(f"Bangumi token_status 查询失败（将回退本地判断）: {e}")
        return None


async def _refresh_bangumi_token(session: AsyncSession, user_id: int, config: Dict[str, Any]) -> bool:
    """刷新Bangumi access token。

    参考ani-rss实现:
    - 当剩余天数 <= 3天时自动刷新
    - 使用refresh_token换取新的access_token

    Returns:
        bool: 刷新成功返回True,失败返回False
    """
    auth = await session.get(orm_models.BangumiAuth, user_id)
    if not auth or not auth.refreshToken:
        return False

    client_id = config.get("client_id")
    client_secret = config.get("client_secret")
    # redirect_uri 优先级：授权时保存的真实回调 > 配置传入(webhookCustomDomain 拼接)。
    # bgm.tv 刷新接口要求 redirect_uri 必填；用授权时一致的值最稳，避免 localhost 回退被拒。
    redirect_uri = getattr(auth, 'redirectUri', None) or config.get("redirect_uri")
    if not getattr(auth, 'redirectUri', None) and config.get("_is_localhost_fallback"):
        logger.warning(
            "Bangumi 刷新使用了 localhost 回退的 redirect_uri，可能与授权时不一致导致刷新被拒。"
            "建议在【设置-自定义域名】填写对外访问地址。"
        )

    if not all([client_id, client_secret, redirect_uri]):
        logger.warning("Bangumi OAuth配置不完整,无法刷新token")
        return False

    try:
        payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": auth.refreshToken,
            "redirect_uri": redirect_uri
        }

        async with httpx.AsyncClient(proxy=config.get("proxy")) as client:
            response = await client.post("https://bgm.tv/oauth/access_token", data=payload)
            response.raise_for_status()
            token_data = response.json()

            # 更新token信息：新 access_token 与轮换后的 refresh_token 都必须落库。
            # bgm.tv 每次刷新会作废旧 refresh_token，若新值丢失将永久掉授权。
            auth.accessToken = token_data["access_token"]
            auth.refreshToken = token_data.get("refresh_token", auth.refreshToken)
            auth.expiresAt = get_now() + timedelta(seconds=token_data.get("expires_in", 604800))
            # 立即提交，避免外层异常回滚导致"旧 refresh 已作废但新 token 未落库"的死局。
            await session.commit()

            logger.info(f"Bangumi token已自动刷新 (用户ID: {user_id})")
            return True

    except Exception as e:
        # 刷新失败时回滚本次未提交的改动，保持 session 干净
        try:
            await session.rollback()
        except Exception:
            pass
        logger.error(f"刷新Bangumi token失败: {e}")
        return False

# ====================================================================
# NEW: API Router for Bangumi specific web endpoints
# ====================================================================

auth_router = APIRouter()


def get_config_manager_dep(request: Request) -> ConfigManager:
    """Dependency to get ConfigManager from app state."""
    return request.app.state.config_manager

class ExchangeCodeRequest(BaseModel):
    """前端 OAuth 回调页面传来的 code 交换请求"""
    code: str = Field(..., description="bgm.tv 返回的授权码")
    state: str = Field(..., description="OAuth state 参数")
    redirect_uri: str = Field(..., description="前端生成的 redirect_uri（必须与授权请求时一致）")


@auth_router.post("/auth/exchange_code", summary="用授权码换取 Token（前端回调页面调用）")
async def exchange_code(
    body: ExchangeCodeRequest,
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager_dep),
    current_user: models.User = Depends(get_current_user),
):
    """
    前端 OAuth 回调页面拿到 code 后调用此接口，完成 token 交换。
    采用 ani-rss 模式：redirect_uri 由前端基于 location.origin 生成，
    确保反向代理环境下地址一致。
    """
    # 验证 state
    user_id = await crud.consume_oauth_state(session, body.state)
    if not user_id or user_id != current_user.id:
        return {"success": False, "message": "State 验证失败，请重新授权"}

    client_id = await config_manager.get("bangumiClientId", "")
    client_secret = await config_manager.get("bangumiClientSecret", "")
    if not client_id or not client_secret:
        return {"success": False, "message": "Bangumi App ID 或 Secret 未配置"}

    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": body.code,
        "redirect_uri": body.redirect_uri,
    }
    try:
        # 获取代理配置（仅当 bangumi 元数据源开启 useProxy 时走代理）
        proxy = None
        proxy_mode = await config_manager.get("proxyMode", "none")
        if proxy_mode == "none" and (await config_manager.get("proxyEnabled", "false")).lower() == "true":
            proxy_mode = "http_socks"
        if proxy_mode == "http_socks":
            proxy_url = await config_manager.get("proxyUrl", "")
            if proxy_url:
                bgm_settings = await crud.get_all_metadata_source_settings(session)
                bgm_setting = next((s for s in bgm_settings if s.get('providerName') == 'bangumi'), None)
                if bgm_setting and bgm_setting.get('useProxy', False):
                    proxy = proxy_url

        async with httpx.AsyncClient(timeout=30.0, proxy=proxy) as client:
            token_response = await client.post("https://bgm.tv/oauth/access_token", data=payload)
            token_response.raise_for_status()
            token_data = token_response.json()
            user_info_response = await client.get(
                "https://api.bgm.tv/v0/me",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            user_info_response.raise_for_status()
            user_info = user_info_response.json()

        avatar_url = user_info.get("avatar", {}).get("large")
        if avatar_url and avatar_url.startswith("//"):
            avatar_url = "https:" + avatar_url

        auth_to_save = {
            "bangumiUserId": user_info.get("id"),
            "nickname": user_info.get("nickname"),
            "username": user_info.get("username"),
            "sign": user_info.get("sign", ""),
            "avatarUrl": avatar_url,
            "accessToken": token_data.get("access_token"),
            "refreshToken": token_data.get("refresh_token"),
            "expiresAt": get_now() + timedelta(seconds=token_data.get("expires_in", 0)),
            "redirectUri": body.redirect_uri,
        }
        await _save_bangumi_auth(session, user_id, auth_to_save)
        await session.commit()
        return {"success": True, "message": "授权成功"}
    except httpx.HTTPStatusError as e:
        logger.error(f"Bangumi token exchange failed: {e.response.text}", exc_info=True)
        return {"success": False, "message": f"Token 交换失败: {e.response.text}"}
    except Exception as e:
        logger.error(f"Bangumi OAuth exchange error: {e}", exc_info=True)
        return {"success": False, "message": f"授权过程发生错误: {str(e)}"}

class BangumiMetadataSource(BaseMetadataSource):
    provider_name = "bangumi"
    api_router = auth_router
    config_keys = ["bangumiClientId", "bangumiClientSecret", "bangumiToken", "authMode", "bangumiApiBaseUrl", "bangumiImageBaseUrl"]

    # ============ 订阅助手能力 ============
    # 注意：这里的「订阅」是【弹幕库内订阅】(自动建库追更)，不是 Bangumi 站内的「在看」。
    # 流程：discover 搜番 → 用户选 → 写 external_calendar_item(bangumiId)
    #       → IncrementalRefreshJob 自动调 auto_search_and_import_task 在弹幕库内整剧建库 + 持续追更。
    supports_subscription = True
    handled_domains = ["bgm.tv", "bangumi.tv"]
    subscription_types = [
        {
            "type": "bangumi_subject",
            "label": "番剧（弹幕库追更）",
            "description": "通过 Bangumi 搜番，订阅后会自动在本地弹幕库建库 + 追更（不会同步到 Bangumi 账号）",
            "payloadSchema": {"fields": [
                {"name": "bangumiId", "type": "string", "required": True, "label": "Bangumi 条目 ID", "placeholder": "例如 12345"},
            ]},
        },
    ]

    DEFAULT_API_BASE_URL = "https://api.bgm.tv"
    DEFAULT_IMAGE_BASE_URL = "https://lain.bgm.tv"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager, scraper_manager: ScraperManager, cache_manager: CacheManager):
        super().__init__(session_factory, config_manager, scraper_manager, cache_manager)
        self._token: Optional[str] = None
        self._config_loaded = False

    @property
    async def test_url(self) -> str:
        """动态地从配置中获取测试URL。"""
        api_base = await self.config_manager.get("bangumiApiBaseUrl", self.DEFAULT_API_BASE_URL)
        return api_base.rstrip('/')

    async def _get_api_base_url(self) -> str:
        """获取 Bangumi API 基础 URL。"""
        url = await self.config_manager.get("bangumiApiBaseUrl", self.DEFAULT_API_BASE_URL)
        return url.rstrip('/')

    async def _rewrite_image_url(self, url: Optional[str]) -> Optional[str]:
        """将图片 URL 中的默认域名替换为用户配置的域名。"""
        if not url:
            return None
        custom_base = await self.config_manager.get("bangumiImageBaseUrl", "")
        if not custom_base or custom_base.rstrip('/') == self.DEFAULT_IMAGE_BASE_URL:
            return url
        # 替换域名部分：https://lain.bgm.tv/... → https://custom.domain/...
        return url.replace(self.DEFAULT_IMAGE_BASE_URL, custom_base.rstrip('/'), 1)

    async def _get_from_cache(self, key: str) -> Optional[Any]:
        """从缓存中获取数据。"""
        _backend = get_cache_backend()
        if _backend is not None:
            try:
                result = await _backend.get(key, region="metadata")
                if result is not None:
                    return result
            except Exception:
                pass
        async with self._session_factory() as session:
            return await crud.get_cache(session, key)

    async def _set_to_cache(self, key: str, value: Any, ttl_key: str, default_ttl: int):
        """将数据设置到缓存中，并从配置中读取TTL。"""
        ttl_seconds = default_ttl
        try:
            ttl_from_config = await self.config_manager.get(ttl_key)
            if ttl_from_config:
                ttl_seconds = int(ttl_from_config)
        except (ValueError, TypeError):
            self.logger.warning(f"无法从配置 '{ttl_key}' 中解析TTL，将使用默认值 {default_ttl} 秒。")

        _backend = get_cache_backend()
        if _backend is not None:
            try:
                await _backend.set(key, value, ttl=ttl_seconds, region="metadata")
            except Exception:
                async with self._session_factory() as session:
                    await crud.set_cache(session, key, value, ttl_seconds)
        else:
            async with self._session_factory() as session:
                await crud.set_cache(session, key, value, ttl_seconds)

    async def _ensure_config(self):
        """从数据库配置中加载个人访问令牌。"""
        if self._config_loaded:
            return
        self._token = await self.config_manager.get("bangumiToken")
        self._config_loaded = True

    async def _get_proxy(self) -> Optional[str]:
        """获取 Bangumi 代理配置。当全局代理启用且 bgm 源设置了 useProxy 时返回代理 URL。"""
        proxy_mode = await self.config_manager.get("proxyMode", "none")
        if proxy_mode == "none":
            if (await self.config_manager.get("proxyEnabled", "false")).lower() == "true":
                proxy_mode = "http_socks"
        if proxy_mode != "http_socks":
            return None
        proxy_url = await self.config_manager.get("proxyUrl", "")
        if not proxy_url:
            return None
        async with self._session_factory() as session:
            all_settings = await crud.get_all_metadata_source_settings(session)
            bgm_setting = next((s for s in all_settings if s.get('providerName') == 'bangumi'), None)
            if bgm_setting and bgm_setting.get('useProxy', False):
                return proxy_url
        return None

    async def _create_client(self, user: models.User) -> httpx.AsyncClient:
        await self._ensure_config()
        headers = {"User-Agent": f"DanmuApiServer/1.0 ({settings.jwt.secret_key[:8]})"}
        if self._token:
            self.logger.debug("Bangumi: 正在使用 Access Token 进行认证。")
            headers["Authorization"] = f"Bearer {self._token}"
        else:
            async with self._session_factory() as session:
                auth_info = await _get_bangumi_auth(session, user.id)

                # 判断是否需要刷新：
                # 1) 已授权但剩余 <=3 天；或 2) 已过期(isExpired)但库里仍有 refresh_token。
                # 关键修复：过期后 _get_bangumi_auth 返回 isAuthenticated=False，旧逻辑会漏刷，
                # 这里改为直接查库里是否存在 refresh_token 来决定能否续期，让过期也能自愈。
                auth_obj = await session.get(orm_models.BangumiAuth, user.id)
                has_refresh = bool(auth_obj and auth_obj.refreshToken)
                near_expiry = auth_info.get("isAuthenticated") and auth_info.get("daysLeft", 999) <= 3
                expired_but_refreshable = (not auth_info.get("isAuthenticated")) and auth_info.get("isExpired") and has_refresh

                if (near_expiry or expired_but_refreshable) and has_refresh:
                    # 构造回调 URL：优先自定义域名，没配才回退 localhost 并标记
                    base_url = await self.config_manager.get("webhookCustomDomain", "")
                    is_localhost_fallback = False
                    if not base_url:
                        base_url = f"http://localhost:{settings.server.port}"
                        is_localhost_fallback = True
                    redirect_uri = f"{base_url.rstrip('/')}/bgm-oauth-callback"

                    config = {
                        "client_id": await self.config_manager.get("bangumiClientId", ""),
                        "client_secret": await self.config_manager.get("bangumiClientSecret", ""),
                        "redirect_uri": redirect_uri,
                        "proxy": await self._get_proxy(),
                        "_is_localhost_fallback": is_localhost_fallback,
                    }
                    # _refresh_bangumi_token 内部已自行 commit
                    refreshed = await _refresh_bangumi_token(session, user.id, config)
                    if refreshed:
                        # 重新获取授权信息（已是新 token）
                        auth_info = await _get_bangumi_auth(session, user.id)
                        self.logger.info(f"Bangumi token已自动刷新 (用户ID: {user.id})")

            if auth_info and auth_info.get("isAuthenticated") and auth_info.get("accessToken"):
                self.logger.debug("Bangumi: 正在使用 OAuth Access Token 进行认证。")
                headers["Authorization"] = f"Bearer {auth_info['accessToken']}"
        api_base_url = await self._get_api_base_url()
        proxy = await self._get_proxy()
        return httpx.AsyncClient(base_url=api_base_url, headers=headers, timeout=20.0, proxy=proxy)

    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        """
        Performs a cached search for Bangumi content.
        It caches the base results for a title.
        """
        parsed = parse_search_keyword(keyword)
        search_title = parsed['title']

        cache_key = f"search_base_{self.provider_name}_{search_title}_{user.id}"
        cached_results = await self._get_from_cache(cache_key)
        if cached_results:
            self.logger.info(f"Bangumi: 从缓存中命中基础搜索结果 (title='{search_title}')")
            return [models.MetadataDetailsResponse.model_validate(r) for r in cached_results]

        self.logger.info(f"Bangumi: 缓存未命中，正在为标题 '{search_title}' 执行网络搜索...")
        all_results = await self._perform_network_search(search_title, user, mediaType)

        if all_results:
            await self._set_to_cache(cache_key, [r.model_dump() for r in all_results], 'metadata_search_ttl_seconds', 3600)

        return all_results

    async def _perform_network_search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        """Performs the actual network search for Bangumi.

        智能过滤逻辑：
        1. 获取第一个结果的 name 和 name_cn 作为基准
        2. 后续结果只有当其 name 包含基准 name，或 name_cn 包含基准 name_cn 时才认为是相关系列
        3. 直接从搜索结果中提取 name 和 name_cn 作为别名，不需要调用 get_details
        """
        async with await self._create_client(user) as client:
            # 只搜索动画类型 (type=2)
            search_payload = {"keyword": keyword, "filter": {"type": [2]}}
            search_response = await client.post("/v0/search/subjects", json=search_payload)
            if search_response.status_code == 404: return []
            search_response.raise_for_status()

            search_result = BangumiSearchResponse.model_validate(search_response.json())
            if not search_result.data: return []

            # 获取第一个结果作为基准
            first_result = search_result.data[0]
            base_name = first_result.name or ""
            base_name_cn = first_result.name_cn or ""

            # 过滤出相关的系列作品
            related_subjects = [first_result]  # 第一个结果一定是相关的

            for subject in search_result.data[1:]:
                subject_name = subject.name or ""
                subject_name_cn = subject.name_cn or ""

                # 检查是否是相关系列：name 包含基准 name，或 name_cn 包含基准 name_cn
                is_related = False
                if base_name and subject_name and base_name in subject_name:
                    is_related = True
                if base_name_cn and subject_name_cn and base_name_cn in subject_name_cn:
                    is_related = True

                if is_related:
                    related_subjects.append(subject)

            self.logger.info(f"Bangumi: 搜索返回 {len(search_result.data)} 个结果，过滤后保留 {len(related_subjects)} 个相关系列")

            # 直接从搜索结果中提取别名，不需要调用 get_details
            # 每个结果有 name（日文名）和 name_cn（中文名），直接构建返回结果
            results = []
            for subject in related_subjects:
                # 收集别名：name_cn 作为 aliasesCn
                aliases_cn = [subject.name_cn] if subject.name_cn else []

                results.append(models.MetadataDetailsResponse(
                    id=str(subject.id),
                    bangumiId=str(subject.id),
                    title=subject.name_cn or subject.name,
                    type="tv_series",
                    nameJp=subject.name,
                    imageUrl=await self._rewrite_image_url(subject.image_url),
                    aliasesCn=aliases_cn
                ))

            return results

    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        async with await self._create_client(user) as client:
            details_url = f"/v0/subjects/{item_id}"
            details_response = await client.get(details_url)
            if details_response.status_code == 404: return None
            details_response.raise_for_status()

            subject_data = details_response.json()
            subject = BangumiSearchSubject.model_validate(subject_data)
            aliases = subject.aliases

            # 推断媒体类型
            media_type = "tv_series" # 默认为 tv_series
            if subject_data.get("type") == 2: # Anime
                # 如果总集数为1，则认为是电影
                if subject_data.get("eps") == 1:
                    media_type = "movie"
                # 检查标题中是否包含电影关键词
                elif _clean_movie_title(subject.display_name) != subject.display_name:
                    media_type = "movie"

            # 提取年份信息
            year = None
            if subject_data.get("date"):
                try:
                    year = int(subject_data["date"][:4])
                except (ValueError, TypeError):
                    pass

            # 确保 name_cn 也被加入到 aliasesCn 中
            aliases_cn = aliases.get("aliases_cn", [])
            if subject.name_cn and subject.name_cn not in aliases_cn:
                aliases_cn = [subject.name_cn] + aliases_cn

            return models.MetadataDetailsResponse(
                id=str(subject.id), bangumiId=str(subject.id), title=subject.display_name,
                type=media_type, nameJp=subject.name, imageUrl=await self._rewrite_image_url(subject.image_url), details=subject.details_string,
                nameEn=aliases.get("name_en"), nameRomaji=aliases.get("name_romaji"),
                aliasesCn=aliases_cn, year=year
            )

    async def search_aliases(self, keyword: str, user: models.User) -> Set[str]:
        local_aliases: Set[str] = set()
        try:
            async with await self._create_client(user) as client:
                search_payload = {"keyword": keyword, "filter": {"type": [2]}}
                search_response = await client.post("/v0/search/subjects", json=search_payload)
                if search_response.status_code != 200: return set()

                search_result = BangumiSearchResponse.model_validate(search_response.json())
                if not search_result.data: return set()

                best_match = search_result.data[0]
                details_response = await client.get(f"/v0/subjects/{best_match.id}")
                if details_response.status_code != 200: return set()

                details = details_response.json()
                local_aliases.add(details.get('name'))
                local_aliases.add(details.get('name_cn'))
                for item in details.get('infobox', []):
                    if item.get('key') == '别名':
                        if isinstance(item['value'], str): local_aliases.add(item['value'])
                        elif isinstance(item['value'], list):
                            for v_item in item['value']:
                                if isinstance(v_item, dict) and v_item.get('v'): local_aliases.add(v_item['v'])
                self.logger.info(f"Bangumi辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
        except Exception as e:
            self.logger.warning(f"Bangumi辅助搜索失败: {e}")
        return {alias for alias in local_aliases if alias}

    async def get_calendar(self, user: models.User) -> List[Dict[str, Any]]:
        """从 Bangumi /calendar 获取当季全量番剧日历

        集数策略（与 Trakt 一致的"两步走"）：
        - BGM /calendar 接口不返回任何集数字段，因此首次刷新时所有番显示 ?/∞
        - 返回前 fire-and-forget 触发两个后台任务并发拉数据：
          1) /v0/subjects/{id} 取 total_episodes/eps（总集数，TTL 6h）
          2) /v0/episodes?subject_id=X 数 airdate≤today 的条目（已播集数，TTL 12h）
        - 下次刷新即可命中缓存显示真实「已播/总」进度

        加载性能：整体日历结果缓存 1 小时，避免每次刷新都重打 BGM /calendar
        （这是页面卡 3+ 秒的根因——BGM 国内访问偏慢，单次 1.5-3s）
        """
        import httpx
        from datetime import datetime

        # 局部缓存常量（避免污染模块顶层）
        _CACHE_REGION = "metadata"
        _BGM_EPS_KEY = "bgm_subject_eps_{bgm_id}"
        _BGM_EPS_TTL = 6 * 3600
        _BGM_EPS_CONCURRENCY = 5
        # 已播集数缓存（airdate ≤ today 的集数）
        _BGM_AIRED_KEY = "bgm_subject_aired_{bgm_id}"
        _BGM_AIRED_TTL = 12 * 3600  # 12h，平衡新鲜度与请求量
        _BGM_AIRED_CONCURRENCY = 5

        # 整体日历结果缓存（仿 Trakt 同款机制）—— 关键性能优化
        cache_backend = get_cache_backend()
        today = datetime.now().strftime("%Y-%m-%d")
        _CALENDAR_CACHE_KEY = f"bangumi_calendar_{today}"
        _CALENDAR_CACHE_TTL = 3600  # 1 小时
        try:
            cached = await cache_backend.get(_CALENDAR_CACHE_KEY, region=_CACHE_REGION)
            if cached is not None:
                self.logger.debug(f"Bangumi 日历缓存命中（{len(cached)} 条），跳过 API 请求")
                return cached
        except Exception:
            pass

        metadata_logger = logging.getLogger('metadata_responses')
        provider_setting = await self._get_provider_setting()
        log_raw = provider_setting.get('logRawResponses', False)

        await self._ensure_config()
        api_base = await self._get_api_base_url()

        items = []
        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                resp = await client.get(f"{api_base}/calendar")
                resp.raise_for_status()
                raw_text = resp.text

                if log_raw:
                    metadata_logger.info(
                        f"Bangumi Calendar Response: URL={resp.url} | Status={resp.status_code} | Body={raw_text}"
                    )

                bgm_calendar = resp.json()

            for day_group in bgm_calendar:
                weekday = day_group.get("weekday", {}).get("id")
                if not weekday:
                    continue
                for bgm in day_group.get("items", []):
                    images = bgm.get("images") or {}
                    air_date = bgm.get("air_date") or ""
                    year = None
                    if air_date and len(air_date) >= 4 and air_date[:4].isdigit():
                        year = int(air_date[:4])
                    bgm_id = str(bgm.get("id", ""))
                    # 总集数：尝试读个例缓存（由后台任务补全），未命中则 None → 前端 ∞
                    eps_total = None
                    aired_now = None
                    if bgm_id:
                        try:
                            eps_total = await cache_backend.get(
                                _BGM_EPS_KEY.format(bgm_id=bgm_id), region=_CACHE_REGION
                            )
                        except Exception:
                            eps_total = None
                        # 已播集数：尝试读个例缓存（由后台任务补全），未命中则 None → 前端 ?
                        try:
                            aired_now = await cache_backend.get(
                                _BGM_AIRED_KEY.format(bgm_id=bgm_id), region=_CACHE_REGION
                            )
                        except Exception:
                            aired_now = None
                    items.append({
                        "animeTitle": bgm.get("name_cn") or bgm.get("name", ""),
                        "airWeekday": weekday,
                        "origin": "bangumi",
                        "isLocal": False,
                        "bangumiId": bgm_id,
                        "traktId": None,
                        "imageUrl": images.get("common") or images.get("medium"),
                        "rating": bgm.get("rating", {}).get("score"),
                        "rank": bgm.get("rank"),
                        "year": year,
                        "airDate": air_date or None,
                        "latestEpisodeIndex": aired_now,  # 已播集数（缓存命中才有）
                        "episodeCount": eps_total,         # 总集数（缓存命中才有）
                    })
        except Exception as e:
            self.logger.warning(f"Bangumi 日历获取失败: {e}")
            return items

        # 后台慢拉总集数：仅对未命中缓存的 bgm_id 触发
        missing_ids = [it.get("bangumiId") for it in items
                       if it.get("bangumiId") and it.get("episodeCount") is None]
        # 后台慢拉已播集数：仅对未命中 aired 缓存的 bgm_id 触发
        missing_aired_ids = [it.get("bangumiId") for it in items
                             if it.get("bangumiId") and it.get("latestEpisodeIndex") is None]
        if missing_ids or missing_aired_ids:
            import asyncio
            if missing_ids:
                asyncio.create_task(self._fetch_eps_background(
                    missing_ids, api_base,
                    concurrency=_BGM_EPS_CONCURRENCY,
                    cache_key_tpl=_BGM_EPS_KEY,
                    cache_region=_CACHE_REGION,
                    cache_ttl=_BGM_EPS_TTL,
                ))
            if missing_aired_ids:
                asyncio.create_task(self._fetch_aired_episodes_background(
                    missing_aired_ids, api_base,
                    concurrency=_BGM_AIRED_CONCURRENCY,
                    cache_key_tpl=_BGM_AIRED_KEY,
                    cache_region=_CACHE_REGION,
                    cache_ttl=_BGM_AIRED_TTL,
                ))

        # 写入整体日历缓存（1小时），下次刷新瞬间命中，不再走 BGM API
        try:
            await cache_backend.set(
                _CALENDAR_CACHE_KEY, items,
                ttl=_CALENDAR_CACHE_TTL, region=_CACHE_REGION,
            )
        except Exception:
            pass

        return items

    async def get_user_watching_collection(self, user: models.User) -> Dict[str, Dict[str, Any]]:
        """拉取「平台账号下我的在追」列表 — 用于补充 external_calendar_item.platformWatchStatus。

        Bangumi collection type 取值（参考官方文档）：
            1 = 想看 (wish)
            2 = 看过 (collect/done)
            3 = 在看 (do/watching)
            4 = 搁置 (on_hold)
            5 = 抛弃 (dropped)

        本方法默认拉取**全部状态**的动画收藏，让上层决定如何映射展示。

        :return: { bangumi_id: {'status': 'watching'|'wish'|..., 'watchedEps': int|None, 'rating': float|None} }
                  未授权时返回 {}
        """
        # 状态码 → 标准化字符串
        STATUS_MAP = {
            1: "wish",
            2: "done",
            3: "watching",
            4: "on_hold",
            5: "dropped",
        }

        # 检查 OAuth 授权（必须有 access_token 才能调 /v0/users/-/collections）
        async with self._session_factory() as session:
            auth_info = await _get_bangumi_auth(session, user.id)
        if not (auth_info and auth_info.get("isAuthenticated") and auth_info.get("accessToken")):
            self.logger.debug("Bangumi: 未授权，跳过 get_user_watching_collection")
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        try:
            async with await self._create_client(user) as client:
                # subject_type=2 表示动画；不传 type 则返回全部状态
                # /v0/users/-/collections 是私有接口，必须带 Authorization
                offset = 0
                limit = 50  # API 默认 30，最大 50
                page_count = 0
                MAX_PAGES = 20  # 安全上限：最多 1000 条收藏，避免循环失控
                while page_count < MAX_PAGES:
                    resp = await client.get(
                        "/v0/users/-/collections",
                        params={"subject_type": 2, "limit": limit, "offset": offset},
                    )
                    if resp.status_code != 200:
                        self.logger.warning(
                            f"Bangumi 拉取在追列表失败 status={resp.status_code} body={resp.text[:200]}"
                        )
                        break
                    data = resp.json() or {}
                    items = data.get("data") or []
                    if not items:
                        break
                    for it in items:
                        subject = it.get("subject") or {}
                        bgm_id = subject.get("id") or it.get("subject_id")
                        if not bgm_id:
                            continue
                        status_code = it.get("type")
                        status_str = STATUS_MAP.get(status_code)
                        if not status_str:
                            continue
                        result[str(bgm_id)] = {
                            "status": status_str,
                            "watchedEps": it.get("ep_status"),  # 看到第几集
                            "rating": it.get("rate") or None,    # 1-10 的整数评分
                        }
                    total = data.get("total") or 0
                    offset += len(items)
                    if offset >= total or len(items) < limit:
                        break
                    page_count += 1
        except Exception as e:
            self.logger.warning(f"Bangumi get_user_watching_collection 异常: {e}")
            return {}

        self.logger.info(f"Bangumi 在追列表: 共 {len(result)} 条 (status 分布略)")
        return result



    async def _fetch_eps_background(
        self, bgm_ids: List[str], api_base: str,
        concurrency: int, cache_key_tpl: str, cache_region: str, cache_ttl: int,
    ) -> None:
        """后台并发拉取每个 subject 的总集数，写入缓存。
        - 安静失败（后台任务，不抛异常）
        - 优先 total_episodes，回退 eps
        """
        import asyncio
        import httpx

        cache_backend = get_cache_backend()
        sem = asyncio.Semaphore(concurrency)

        async def _one(client: httpx.AsyncClient, bgm_id: str) -> None:
            async with sem:
                try:
                    resp = await client.get(f"{api_base}/v0/subjects/{bgm_id}")
                    if resp.status_code != 200:
                        return
                    data = resp.json() or {}
                    # 优先 total_episodes（"完整最终集数"），回退到 eps
                    total = data.get("total_episodes") or data.get("eps")
                    if isinstance(total, int) and total > 0:
                        try:
                            await cache_backend.set(
                                cache_key_tpl.format(bgm_id=bgm_id),
                                total, ttl=cache_ttl, region=cache_region,
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                await asyncio.gather(*(_one(client, bid) for bid in bgm_ids), return_exceptions=True)
            self.logger.debug(f"Bangumi 后台拉取 total_episodes 完成: {len(bgm_ids)} 个 subject")
        except Exception as e:
            self.logger.debug(f"Bangumi 后台拉取 total_episodes 失败: {e}")

    async def _fetch_aired_episodes_background(
        self, bgm_ids: List[str], api_base: str,
        concurrency: int, cache_key_tpl: str, cache_region: str, cache_ttl: int,
    ) -> None:
        """后台并发拉取每个 subject 的"已播集数"，写入缓存。

        BGM 没有原生"当前集"字段，唯一办法是调 /v0/episodes 拿全部集，
        然后数 airdate ≤ today 的条目数 = 已播集数（仿 ani-rss BgmUtil.getEpisodes）。

        - 安静失败（后台任务，不抛异常）
        - 并发受 concurrency 限制，避免触发 BGM 限流
        - type=0 仅取正篇（排除 OVA/SP/番外）
        - limit=200 足以覆盖绝大多数番（最长番剧《海螺小姐》也就 2700+ 集，分页另说）
        """
        import asyncio
        from datetime import datetime, timezone, timedelta
        import httpx

        cache_backend = get_cache_backend()
        sem = asyncio.Semaphore(concurrency)
        # 用 UTC+8（BGM 数据基本是日本/中国时区），避免时差导致今日已播被误算
        today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")

        async def _one(client: httpx.AsyncClient, bgm_id: str) -> None:
            async with sem:
                try:
                    resp = await client.get(
                        f"{api_base}/v0/episodes",
                        params={"subject_id": bgm_id, "type": 0, "limit": 200, "offset": 0},
                    )
                    if resp.status_code != 200:
                        return
                    data = resp.json() or {}
                    eps_list = data.get("data") or []
                    if not isinstance(eps_list, list):
                        return
                    aired = sum(
                        1 for e in eps_list
                        if isinstance(e, dict) and e.get("airdate") and e.get("airdate") <= today
                    )
                    if aired > 0:
                        try:
                            await cache_backend.set(
                                cache_key_tpl.format(bgm_id=bgm_id),
                                aired, ttl=cache_ttl, region=cache_region,
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                await asyncio.gather(*(_one(client, bid) for bid in bgm_ids), return_exceptions=True)
            self.logger.debug(f"Bangumi 后台拉取 aired_episodes 完成: {len(bgm_ids)} 个 subject")
        except Exception as e:
            self.logger.debug(f"Bangumi 后台拉取 aired_episodes 失败: {e}")


    async def _get_provider_setting(self) -> Dict[str, Any]:
        """获取当前源的设置"""
        async with self._session_factory() as session:
            settings = await crud.get_all_metadata_source_settings(session)
            return next((s for s in settings if s['providerName'] == self.provider_name), {})

    async def check_connectivity(self) -> Dict[str, str]:
        """检查Bangumi源配置状态"""
        try:
            await self._ensure_config()

            # 1. 优先检查 Access Token 模式
            if self._token:
                return {"code": "ok", "message": "Access Token 模式 (已配置)"}

            # 2. 检查 OAuth 模式
            client_id = await self.config_manager.get("bangumiClientId", "")
            client_secret = await self.config_manager.get("bangumiClientSecret", "")

            if client_id and client_secret:
                # 检查是否有用户已授权
                try:
                    async with self._session_factory() as session:
                        stmt = select(func.count(orm_models.BangumiAuth.userId)).where(
                            orm_models.BangumiAuth.expiresAt > get_now()
                        )
                        valid_token_count = (await session.execute(stmt)).scalar_one()

                    if valid_token_count > 0:
                        return {"code": "ok", "message": f"OAuth 模式 ({valid_token_count}个用户已授权)"}
                    else:
                        return {"code": "warning", "message": "OAuth 模式 (App已配置，等待用户授权)"}
                except Exception:
                    return {"code": "ok", "message": "OAuth 模式 (App已配置)"}
            elif client_id:
                return {"code": "warning", "message": "OAuth 模式 (App ID已填，App Secret 未填)"}
            else:
                return {"code": "unconfigured", "message": "未配置 (请填写Access Token 或 OAuth App信息)"}

        except Exception as e:
            return {"code": "error", "message": f"配置检查失败: {e}"}

    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: models.User, request: Request) -> Any:
        if action_name == "get_auth_state":
            async with self._session_factory() as session:
                auth_info = await _get_bangumi_auth(session, user.id)

                # 自动刷新token (参考ani-rss: 剩余天数<=3天时刷新)
                if auth_info.get("isAuthenticated") and auth_info.get("daysLeft", 999) <= 3:
                    base_url = await self.config_manager.get("webhookCustomDomain", "")
                    if not base_url:
                        base_url = f"http://localhost:{settings.server.port}"
                    redirect_uri = f"{base_url.rstrip('/')}/bgm-oauth-callback"
                    config = {
                        "client_id": await self.config_manager.get("bangumiClientId", ""),
                        "client_secret": await self.config_manager.get("bangumiClientSecret", ""),
                        "redirect_uri": redirect_uri
                    }
                    refreshed = await _refresh_bangumi_token(session, user.id, config)
                    if refreshed:
                        await session.commit()
                        auth_info = await _get_bangumi_auth(session, user.id)
                        auth_info["refreshed"] = True

                return auth_info
        elif action_name == "get_auth_url":
            # 新模式：前端传来 redirect_uri，后端只负责生成 state 和拼接 auth URL
            async with self._session_factory() as session:
                client_id = await self.config_manager.get("bangumiClientId", "")
                client_secret = await self.config_manager.get("bangumiClientSecret", "")
                if not client_id:
                    raise ValueError("Bangumi App ID 未在设置中配置，请先在元数据源设置中填写。")
                if not client_secret:
                    raise ValueError("Bangumi App Secret 未在设置中配置，请先在元数据源设置中填写。")

                redirect_uri = payload.get("redirect_uri", "")
                if not redirect_uri:
                    raise ValueError("redirect_uri 不能为空")

                state = await crud.create_oauth_state(session, user.id)
                params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state}
                auth_url = f"https://bgm.tv/oauth/authorize?{urlencode(params)}"
                return {"url": auth_url, "state": state}
        elif action_name == "refresh_token":
            # 原地刷新 token（不跳转 bgm.tv，使用 refresh_token 续期）
            async with self._session_factory() as session:
                auth_info = await _get_bangumi_auth(session, user.id)
                if not auth_info.get("isAuthenticated"):
                    return {"success": False, "message": "当前未授权，请先完成 OAuth 授权"}

                client_id = await self.config_manager.get("bangumiClientId", "")
                client_secret = await self.config_manager.get("bangumiClientSecret", "")
                if not client_id or not client_secret:
                    return {"success": False, "message": "Bangumi App ID 或 Secret 未配置"}

                # redirect_uri 由 _refresh_bangumi_token 内部从 auth.redirectUri 读取
                config = {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": "",  # fallback，优先用 DB 中保存的
                }
                refreshed = await _refresh_bangumi_token(session, user.id, config)
                if refreshed:
                    await session.commit()
                    auth_info = await _get_bangumi_auth(session, user.id)
                    return {"success": True, "message": "Token 续期成功", "authInfo": auth_info}
                else:
                    return {"success": False, "message": "Token 续期失败，refresh_token 可能已过期，请重新授权"}
        elif action_name == "logout":
            async with self._session_factory() as session:
                await _delete_bangumi_auth(session, user.id)
                await session.commit()
            return {"message": "注销成功"}
        else:
            return await super().execute_action(action_name, payload, user, request)

    # ============ 订阅助手实现 ============

    async def check_subscription_capability(self, user=None) -> Dict[str, Any]:
        """Bangumi 订阅可用性：公共搜索 API 即可（无 OAuth 也能搜）。"""
        return {
            "available": True,
            "authRequired": False,
            "authStatus": "valid",
            "reason": None,
            "subscriptionTypes": self.subscription_types,
        }

    async def discover_subscription_targets(self, query: str, subscription_type: str = "", user=None) -> List[Dict[str, Any]]:
        """搜 Bangumi 番剧条目候选；复用 search()。无登录用户时构造一个匿名占位。"""
        query = (query or "").strip()
        if not query:
            return []
        # search() 需要 user.id 做缓存键；订阅探测阶段允许 user 缺省，构造匿名兜底
        u = user or models.User(id=0, username="__sub_discover__")
        try:
            results = await self.search(query, user=u)
        except Exception as e:
            self.logger.warning(f"Bangumi: 订阅 discover 搜索失败: {type(e).__name__}: {e}")
            return []
        out = []
        for r in results[:20]:
            if not r.id:
                continue
            year = f" · {r.year}" if r.year else ""
            desc = (r.details or "")[:80]
            out.append({
                "type": "bangumi_subject",
                "title": r.title or f"Bangumi {r.id}",
                "cover": r.imageUrl,
                "description": f"Bangumi ID {r.id}{year}" + (f" · {desc}" if desc else ""),
                "payload": {
                    "bangumiId": str(r.id),
                    "title": r.title or "",
                    "year": r.year,
                    "imageUrl": r.imageUrl,
                },
            })
        return out

    async def validate_subscription_payload(self, subscription_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """校验 Bangumi 订阅 payload；统一返回写入 external_calendar_item 的标准结构。"""
        if subscription_type != "bangumi_subject":
            raise ValueError(f"Bangumi 暂不支持订阅类型: {subscription_type}")
        bgm_id = (payload or {}).get("bangumiId") or ""
        if not str(bgm_id).strip():
            raise ValueError("缺少 bangumiId")
        title = (payload or {}).get("title") or ""
        return {
            "provider": self.provider_name,
            "externalId": f"bgm-{bgm_id}",
            "title": title,
            "animeType": "tv_series",
            "subscriptionType": "bangumi_subject",
            "extraData": {
                "bangumiId": str(bgm_id),
                "year": (payload or {}).get("year"),
                "imageUrl": (payload or {}).get("imageUrl"),
            },
        }
