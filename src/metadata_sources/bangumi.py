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
from src.utils import parse_search_keyword
from src.utils import clean_movie_title as _clean_movie_title
from src.services import ScraperManager
from .base import BaseMetadataSource

logger = logging.getLogger(__name__)

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
    redirect_uri = config.get("redirect_uri")

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

        async with httpx.AsyncClient() as client:
            response = await client.post("https://bgm.tv/oauth/access_token", json=payload)
            response.raise_for_status()
            token_data = response.json()

            # 更新token信息
            auth.accessToken = token_data["access_token"]
            auth.refreshToken = token_data.get("refresh_token", auth.refreshToken)
            auth.expiresAt = get_now() + timedelta(seconds=token_data.get("expires_in", 604800))
            await session.flush()

            logger.info(f"Bangumi token已自动刷新 (用户ID: {user_id})")
            return True

    except Exception as e:
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
        async with httpx.AsyncClient(timeout=30.0) as client:
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
    test_url = "https://bgm.tv"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager, scraper_manager: ScraperManager, cache_manager: CacheManager):
        super().__init__(session_factory, config_manager, scraper_manager, cache_manager)
        self.api_base_url = "https://api.bgm.tv"
        self._token: Optional[str] = None
        self._config_loaded = False

    async def _get_from_cache(self, key: str) -> Optional[Any]:
        """从缓存中获取数据。"""
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
        
        async with self._session_factory() as session:
            await crud.set_cache(session, key, value, ttl_seconds)

    async def _ensure_config(self):
        """从数据库配置中加载个人访问令牌。"""
        if self._config_loaded:
            return
        self._token = await self.config_manager.get("bangumiToken")
        self._config_loaded = True
        
    async def _create_client(self, user: models.User) -> httpx.AsyncClient:
        await self._ensure_config()
        headers = {"User-Agent": f"DanmuApiServer/1.0 ({settings.jwt.secret_key[:8]})"}
        if self._token:
            self.logger.debug("Bangumi: 正在使用 Access Token 进行认证。")
            headers["Authorization"] = f"Bearer {self._token}"
        else:
            async with self._session_factory() as session:
                auth_info = await _get_bangumi_auth(session, user.id)

                # 自动刷新token (参考ani-rss: 剩余天数<=3天时刷新)
                if auth_info.get("isAuthenticated") and auth_info.get("daysLeft", 999) <= 3:
                    # 构造回调URL（无 request 上下文，直接用配置拼接）
                    # 注意：refresh token 时 bgm.tv 不严格校验 redirect_uri，
                    # 但仍需传一个合法值，这里用 localhost fallback 即可
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
                        # 重新获取授权信息
                        auth_info = await _get_bangumi_auth(session, user.id)
                        self.logger.info(f"Bangumi token已自动刷新 (用户ID: {user.id})")

            if auth_info and auth_info.get("isAuthenticated") and auth_info.get("accessToken"):
                self.logger.debug("Bangumi: 正在使用 OAuth Access Token 进行认证。")
                headers["Authorization"] = f"Bearer {auth_info['accessToken']}"
        return httpx.AsyncClient(base_url="https://api.bgm.tv", headers=headers, timeout=20.0)

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
                    imageUrl=subject.image_url,
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
                type=media_type, nameJp=subject.name, imageUrl=subject.image_url, details=subject.details_string,
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

    async def check_connectivity(self) -> str:
        """检查Bangumi源配置状态"""
        try:
            await self._ensure_config()

            # 1. 优先检查 Access Token
            if self._token:
                return "配置正常 (已配置Access Token)"

            # 2. 检查OAuth配置
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
                        return f"配置正常 (OAuth已配置，{valid_token_count}个用户已授权)"
                    else:
                        return "配置正常 (OAuth已配置，等待用户授权)"
                except Exception:
                    return "配置正常 (OAuth已配置)"
            elif client_id:
                return "配置不完整 (缺少Client Secret)"
            else:
                return "未配置 (缺少OAuth配置)"

        except Exception as e:
            return f"配置检查失败: {e}"

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
                if not client_id:
                    raise ValueError("Bangumi App ID 未在设置中配置。")

                redirect_uri = payload.get("redirect_uri", "")
                if not redirect_uri:
                    raise ValueError("redirect_uri 不能为空")

                state = await crud.create_oauth_state(session, user.id)
                params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state}
                auth_url = f"https://bgm.tv/oauth/authorize?{urlencode(params)}"
                return {"url": auth_url, "state": state}
        elif action_name == "logout":
            async with self._session_factory() as session:
                await _delete_bangumi_auth(session, user.id)
                await session.commit()
            return {"message": "注销成功"}
        else:
            return await super().execute_action(action_name, payload, user, request)
