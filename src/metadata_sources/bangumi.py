import asyncio
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud, models, orm_models, security
from ..config import settings
from ..config_manager import ConfigManager
from ..database import get_db_session
from .base import BaseMetadataSource

logger = logging.getLogger(__name__)

def _clean_movie_title(title: Optional[str]) -> Optional[str]:
    if not title: return None
    phrases_to_remove = ["劇場版", "the movie"]
    cleaned_title = title
    for phrase in phrases_to_remove:
        cleaned_title = re.sub(r'\s*' + re.escape(phrase) + r'\s*:?', '', cleaned_title, flags=re.IGNORECASE)
    cleaned_title = re.sub(r'\s{2,}', ' ', cleaned_title).strip().strip(':- ')
    return cleaned_title

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
            try: parts.append(datetime.fromisoformat(self.date).strftime('%Y年%m月%d日'))
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
    
    if auth.expiresAt and auth.expiresAt.replace(tzinfo=None) < datetime.utcnow():
        return {"isAuthenticated": False, "isExpired": True}

    return {
        "isAuthenticated": True, "bangumiUserId": auth.bangumiUserId,
        "nickname": auth.nickname, "avatarUrl": auth.avatarUrl,
        "authorizedAt": auth.authorizedAt, "expiresAt": auth.expiresAt,
        "accessToken": auth.accessToken
    }

async def _save_bangumi_auth(session: AsyncSession, user_id: int, auth_data: Dict[str, Any]):
    """保存或更新用户的Bangumi授权信息。"""
    existing_auth = await session.get(orm_models.BangumiAuth, user_id)
    
    if 'expiresAt' in auth_data and isinstance(auth_data['expiresAt'], datetime) and auth_data['expiresAt'].tzinfo is None:
        auth_data['expiresAt'] = auth_data['expiresAt'].replace(tzinfo=timezone.utc)

    if existing_auth:
        for key, value in auth_data.items():
            setattr(existing_auth, key, value)
        existing_auth.authorizedAt = datetime.now(timezone.utc)
    else:
        new_auth = orm_models.BangumiAuth(userId=user_id, **auth_data, authorizedAt=datetime.now(timezone.utc))
        session.add(new_auth)
    await session.flush()

async def _delete_bangumi_auth(session: AsyncSession, user_id: int):
    """删除用户的Bangumi授权信息。"""
    stmt = delete(orm_models.BangumiAuth).where(orm_models.BangumiAuth.userId == user_id)
    await session.execute(stmt)

# ====================================================================
# NEW: API Router for Bangumi specific web endpoints
# ====================================================================

auth_router = APIRouter()


def get_config_manager_dep(request: Request) -> ConfigManager:
    """Dependency to get ConfigManager from app state."""
    return request.app.state.config_manager

@auth_router.get("/auth/callback", summary="Bangumi OAuth回调处理", include_in_schema=False, name="bangumi_auth_callback")
async def bangumi_auth_callback(request: Request, code: str = Query(...), state: str = Query(...), session: AsyncSession = Depends(get_db_session), config_manager: ConfigManager = Depends(get_config_manager_dep)):
    user_id = await crud.consume_oauth_state(session, state)
    if not user_id: return HTMLResponse("<html><body>State Mismatch. Authorization failed. Please try again.</body></html>", status_code=400)
    client_id, client_secret = await asyncio.gather(config_manager.get("bangumiClientId"), config_manager.get("bangumiClientSecret"))
    if not client_id or not client_secret: return HTMLResponse("<html><body>Server configuration error: Bangumi App ID or Secret is not set.</body></html>", status_code=500)
    
    # 修正：使用 FastAPI 的 url_for 来生成回调URL，以确保其在反向代理后也能正确工作。
    redirect_uri = str(request.url_for('bangumi_auth_callback'))

    payload = {"grant_type": "authorization_code", "client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect_uri}
    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post("https://bgm.tv/oauth/access_token", data=payload)
            token_response.raise_for_status()
            token_data = token_response.json()
            user_info_response = await client.get("/v0/me", headers={"Authorization": f"Bearer {token_data['access_token']}"})
            user_info_response.raise_for_status()
            user_info = user_info_response.json()
        auth_to_save = {"bangumiUserId": user_info.get("id"), "nickname": user_info.get("nickname"), "avatarUrl": user_info.get("avatar", {}).get("large"), "accessToken": token_data.get("access_token"), "refreshToken": token_data.get("refresh_token"), "expiresAt": datetime.now(timezone.utc) + timedelta(seconds=token_data.get("expires_in", 0))}
        await _save_bangumi_auth(session, user_id, auth_to_save)
        await session.commit()
        return HTMLResponse("""
            <html><head><title>授权处理中...</title></head><body><script type="text/javascript">
              try { window.opener.postMessage('BANGUMI-OAUTH-COMPLETE', '*'); } catch(e) { console.error(e); }
              window.close();
            </script><p>授权成功，请关闭此窗口。</p></body></html>
            """)
    except httpx.HTTPStatusError as e:
        logger.error(f"Bangumi token exchange failed: {e.response.text}", exc_info=True)
        return HTMLResponse(f"<html><body>Token exchange failed: {e.response.text}</body></html>", status_code=500)
    except Exception as e:
        logger.error(f"An unexpected error occurred during Bangumi callback: {e}", exc_info=True)
        return HTMLResponse("<html><body>An unexpected error occurred.</body></html>", status_code=500)

class BangumiMetadataSource(BaseMetadataSource):
    provider_name = "bangumi"
    api_router = auth_router

    async def _create_client(self, user: models.User) -> httpx.AsyncClient:
        async with self._session_factory() as session:
            auth_info = await _get_bangumi_auth(session, user.id)
        
        headers = {"User-Agent": f"DanmuApiServer/1.0 ({settings.jwt.secret_key[:8]})"}
        if auth_info and auth_info.get("isAuthenticated") and auth_info.get("accessToken"):
            headers["Authorization"] = f"Bearer {auth_info['accessToken']}"
        
        return httpx.AsyncClient(base_url="https://api.bgm.tv", headers=headers, timeout=20.0)

    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        async with await self._create_client(user) as client:
            search_payload = {"keyword": keyword, "filter": {"type": [2]}}
            search_response = await client.post("/v0/search/subjects", json=search_payload)
            if search_response.status_code == 404: return []
            search_response.raise_for_status()
            
            search_result = BangumiSearchResponse.model_validate(search_response.json())
            if not search_result.data: return []

            tasks = [self.get_details(str(subject.id), user) for subject in search_result.data]
            detailed_results = await asyncio.gather(*tasks)
            return [res for res in detailed_results if res]

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

            return models.MetadataDetailsResponse(
                id=str(subject.id), bangumiId=str(subject.id), title=subject.display_name,
                type=media_type, nameJp=subject.name, imageUrl=subject.image_url, details=subject.details_string,
                nameEn=aliases.get("name_en"), nameRomaji=aliases.get("name_romaji"),
                aliasesCn=aliases.get("aliases_cn", [])
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
        """检查与Bangumi API的连接性，并遵循代理设置。"""
        proxy_to_use = None
        try:
            # 使用会话工厂获取数据库会话
            async with self._session_factory() as session:
                # 获取全局代理设置
                proxy_url = await crud.get_config_value(session, "proxy_url", "")
                proxy_enabled_str = await crud.get_config_value(session, "proxy_enabled", "false")
                proxy_enabled_globally = proxy_enabled_str.lower() == 'true'

                # 如果全局代理启用，则检查此源的代理设置
                if proxy_enabled_globally and proxy_url:
                    source_setting = await crud.get_metadata_source_setting_by_name(session, self.provider_name)
                    if source_setting and source_setting.get('useProxy', False):
                        proxy_to_use = proxy_url
                        self.logger.debug(f"Bangumi: 连接性检查将使用代理: {proxy_to_use}")

            async with httpx.AsyncClient(timeout=10.0, proxy=proxy_to_use) as client:
                response = await client.get("https://api.bgm.tv/v0/ping")
                return "连接成功" if response.status_code == 204 else f"连接失败 (状态码: {response.status_code})"
        except httpx.ProxyError as e:
            self.logger.error(f"Bangumi: 连接性检查代理错误: {e}")
            return "连接失败 (代理错误)"
        except Exception as e:
            self.logger.error(f"Bangumi: 连接性检查发生未知错误: {e}", exc_info=True)
            return "连接失败"

    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: models.User, request: Request) -> Any:
        if action_name == "get_auth_state":
            async with self._session_factory() as session:
                auth_info = await _get_bangumi_auth(session, user.id)
                return auth_info
        elif action_name == "get_auth_url":
            async with self._session_factory() as session:
                client_id = await self.config_manager.get("bangumiClientId", "")
                if not client_id:
                    raise ValueError("Bangumi App ID 未在设置中配置。")
                
                # 修正：使用 FastAPI 的 url_for 来生成回调URL，以确保其在反向代理后也能正确工作。
                redirect_uri = str(request.url_for('bangumi_auth_callback'))
                
                state = await crud.create_oauth_state(session, user.id)
                params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state}
                auth_url = f"https://bgm.tv/oauth/authorize?{urlencode(params)}"
                return {"url": auth_url}
        elif action_name == "logout":
            async with self._session_factory() as session:
                await _delete_bangumi_auth(session, user.id)
                await session.commit()
            return {"message": "注销成功"}
        else:
            return await super().execute_action(action_name, payload, user, request)

