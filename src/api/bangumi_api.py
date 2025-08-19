import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union
from typing import Set
from urllib.parse import urlencode, quote
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

from .. import crud, models, security
from ..config import settings
from ..database import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter()

def _clean_movie_title(title: Optional[str]) -> Optional[str]:
    """
    从标题中移除 "剧场版" 或 "The Movie" 等词语。
    """
    if not title:
        return None
    # A list of phrases to remove, case-insensitively
    phrases_to_remove = ["劇場版", "the movie"]
    
    cleaned_title = title
    for phrase in phrases_to_remove:
        # This regex removes the phrase, optional surrounding whitespace, and an optional trailing colon.
        cleaned_title = re.sub(r'\s*' + re.escape(phrase) + r'\s*:?', '', cleaned_title, flags=re.IGNORECASE)

    # Clean up any double spaces that might result from the removal and leading/trailing separators
    cleaned_title = re.sub(r'\s{2,}', ' ', cleaned_title).strip().strip(':- ')
    
    return cleaned_title
# --- Pydantic Models for Bangumi API ---

class BangumiTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: int

class BangumiUser(BaseModel):
    id: int
    username: str
    nickname: str
    avatar: Dict[str, str]

class BangumiApiSearchResult(BaseModel):
    id: str
    title: str
    details: Optional[str] = None
    imageUrl: Optional[str] = None

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
        """在模型验证后清理主标题和中文标题。"""
        self.name = _clean_movie_title(self.name)
        self.name_cn = _clean_movie_title(self.name_cn)
        return self

    @property
    def display_name(self) -> str:
        return self.name_cn or self.name

    @property
    def image_url(self) -> Optional[str]:
        """从 images 字典中获取一个合适的图片URL。"""
        if self.images:
            # 优先顺序: common > large > medium > small > grid
            for size in ["common", "large", "medium", "small", "grid"]:
                if url := self.images.get(size):
                    return url
        return None

    @property
    def aliases(self) -> Dict[str, Any]:
        """从 infobox 提取别名，并进行分类。"""
        data = {
            "name_en": None,
            "name_romaji": None,
            "aliases_cn": []
        }
        if not self.infobox:
            return data

        def extract_value(value: Any) -> List[str]:
            if isinstance(value, str):
                return [v.strip() for v in value.split('/') if v.strip()]
            elif isinstance(value, list):
                # 确保 v 是一个字典并且有 'v' 键
                return [v.get("v", "").strip() for v in value if isinstance(v, dict) and v.get("v")]
            return []

        all_raw_aliases = []

        for item in self.infobox:
            key, value = item.key.strip(), item.value
            if key == "英文名" and isinstance(value, str):
                data["name_en"] = _clean_movie_title(value.strip())
            elif key == "罗马字" and isinstance(value, str):
                data["name_romaji"] = _clean_movie_title(value.strip())
            elif key == "别名":
                all_raw_aliases.extend(extract_value(value))

        # 1. 过滤出纯中文的别名
        # 使用正则表达式匹配是否包含中文字符
        chinese_char_pattern = re.compile(r'[\u4e00-\u9fa5]')
        cleaned_aliases = [_clean_movie_title(alias) for alias in all_raw_aliases]
        data["aliases_cn"] = [alias for alias in cleaned_aliases if alias and chinese_char_pattern.search(alias)]
        
        # 2. 去重
        data["aliases_cn"] = list(dict.fromkeys(data["aliases_cn"]))
        return data

    @property
    def details_string(self) -> str:
        """从 infobox 和 date 构建一个详细描述字符串。"""
        parts = []
        if self.date:
            try:
                dt = datetime.fromisoformat(self.date)
                parts.append(dt.strftime('%Y年%m月%d日'))
            except (ValueError, TypeError):
                parts.append(self.date)

        if self.infobox:
            staff_keys_of_interest = ["导演", "原作", "脚本", "人物设定", "系列构成", "总作画监督"]
            staff_found = {}
            for item in self.infobox:
                if item.key in staff_keys_of_interest:
                    value_str = ""
                    if isinstance(item.value, str):
                        value_str = item.value.strip()
                    elif isinstance(item.value, list):
                        names = [v.get("v", "").strip() for v in item.value if isinstance(v, dict) and v.get("v")]
                        value_str = "、".join(names)
                    if value_str:
                        staff_found[item.key] = value_str
            
            for key in staff_keys_of_interest:
                if key in staff_found and len(parts) < 5: # 限制总字段数，避免过长
                    parts.append(staff_found[key])
        
        return " / ".join(parts)

class BangumiSearchResponse(BaseModel):
    data: Optional[List[BangumiSearchSubject]] = None

class BangumiAuthState(BaseModel):
    is_authenticated: bool
    nickname: Optional[str] = None
    avatar_url: Optional[str] = None
    bangumi_user_id: Optional[int] = None
    authorized_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

# --- Helper Function ---

async def _create_bangumi_client(session: AsyncSession, user_id: int) -> httpx.AsyncClient:
    """Creates an httpx.AsyncClient with Bangumi auth."""
    auth_info = await crud.get_bangumi_auth(session, user_id)
    if not auth_info or not auth_info.get("access_token"):
        raise ValueError("Bangumi not authenticated")

    expires_at = auth_info.get("expires_at")
    if expires_at and datetime.now() >= expires_at:
        try:
            client_id_task = crud.get_config_value(session, "bangumi_client_id", "")
            client_secret_task = crud.get_config_value(session, "bangumi_client_secret", "")
            client_id, client_secret = await asyncio.gather(client_id_task, client_secret_task)
            if not client_id or not client_secret:
                raise ValueError("Bangumi App ID/Secret 未配置，无法刷新Token。")

            async with httpx.AsyncClient() as client:
                token_data = {
                    "grant_type": "refresh_token", "client_id": client_id,
                    "client_secret": client_secret, "refresh_token": auth_info["refresh_token"],
                }
                response = await client.post("https://bgm.tv/oauth/access_token", data=token_data)
                response.raise_for_status()
                new_token_info = BangumiTokenResponse.model_validate(response.json())

                auth_info["access_token"] = new_token_info.access_token
                auth_info["refresh_token"] = new_token_info.refresh_token
                auth_info["expires_at"] = datetime.now() + timedelta(seconds=new_token_info.expires_in)
                await crud.save_bangumi_auth(session, user_id, auth_info)
        except Exception as e:
            logger.error(f"刷新 Bangumi token 失败: {e}", exc_info=True)
            raise ValueError("Bangumi token 已过期且刷新失败")

    headers = {"Authorization": f"Bearer {auth_info['access_token']}", "User-Agent": "l429609201/misaka_danmu_server(https://github.com/l429609201/misaka_danmu_server)"}
    return httpx.AsyncClient(headers=headers, timeout=20.0)

async def get_bangumi_client(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
) -> httpx.AsyncClient:
    """依赖项：创建一个带有 Bangumi 授权的 httpx 客户端。"""
    auth_info = await crud.get_bangumi_auth(session, current_user.id)
    if not auth_info or not auth_info.get("access_token"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bangumi not authenticated")
    try:
        return await _create_bangumi_client(session, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

# --- API Endpoints ---

async def get_bangumi_subject_details_logic(subject_id: int, client: httpx.AsyncClient) -> "models.MetadataDetailsResponse":
    """获取指定 Bangumi ID 的作品详情。"""
    details_url = f"https://api.bgm.tv/v0/subjects/{subject_id}"
    details_response = await client.get(details_url)
    if details_response.status_code == 404:
        raise ValueError("未找到指定的 Bangumi 作品。")
    details_response.raise_for_status()

    subject_data = details_response.json()
    subject = BangumiSearchSubject.model_validate(subject_data)
    aliases = subject.aliases

    return models.MetadataDetailsResponse(
        id=str(subject.id),
        bangumiId=str(subject.id),
        title=subject.display_name,
        nameJp=subject.name,
        imageUrl=subject.image_url,
        details=subject.details_string,
        nameEn=aliases.get("name_en"),
        nameRomaji=aliases.get("name_romaji"),
        aliasesCn=aliases.get("aliases_cn", [])
    )

@router.get("/subjects/{subject_id}", response_model=models.MetadataDetailsResponse, summary="获取 Bangumi 作品详情")
async def get_bangumi_subject_details(
    subject_id: int = Path(..., description="Bangumi 作品ID"),
    client: httpx.AsyncClient = Depends(get_bangumi_client),
):
    """获取指定 Bangumi ID 的作品详情。"""
    try:
        return await get_bangumi_subject_details_logic(subject_id, client)
    except Exception as e:
        logger.error(f"获取 Bangumi subject {subject_id} 详情失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取 Bangumi 详情失败。")


@router.get("/auth/url", response_model=Dict[str, str], summary="获取 Bangumi OAuth 授权链接")
async def get_bangumi_auth_url(
    request: Request,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """生成用于 Bangumi OAuth 流程的重定向 URL。"""
    # 1. 创建并存储一个唯一的、有有效期的 state
    state = await crud.create_oauth_state(session, current_user.id)

    client_id = await crud.get_config_value(session, "bangumi_client_id", "")
    if not client_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bangumi App ID 未在设置中配置。")

    # 构建回调 URL，它将指向我们的 /auth/callback 端点
    redirect_uri = request.url_for('bangumi_auth_callback')
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": str(redirect_uri),
        "state": state, # 2. 将 state 添加到授权 URL
    }
    query_string = urlencode(params)
    auth_url = f"https://bgm.tv/oauth/authorize?{query_string}"
    return {"url": auth_url}

@router.get("/auth/callback", response_class=HTMLResponse, summary="Bangumi OAuth 回调", name="bangumi_auth_callback")
async def bangumi_auth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_db_session),
    request: Request = None
):
    """处理来自 Bangumi 的 OAuth 回调，用 code 交换 token。"""
    # 1. 验证并消费 state，获取发起授权的用户ID
    user_id = await crud.consume_oauth_state(session, state)
    if user_id is None:
        logger.error(f"Bangumi OAuth回调失败：无效或已过期的 state '{state}'")
        return HTMLResponse("<h1>认证失败：无效的请求状态，请重新发起授权。</h1>", status_code=400)

    # 2. 从数据库获取用户信息
    user_dict = await crud.get_user_by_id(session, user_id)
    if not user_dict:
        logger.error(f"Bangumi OAuth回调失败：找不到与 state 关联的用户 ID '{user_id}'")
        return HTMLResponse("<h1>认证失败：找不到与此授权请求关联的用户。</h1>", status_code=404)
    user = models.User.model_validate(user_dict)

    client_id_task = crud.get_config_value(session, "bangumi_client_id", "")
    client_secret_task = crud.get_config_value(session, "bangumi_client_secret", "")
    client_id, client_secret = await asyncio.gather(client_id_task, client_secret_task)
    if not client_id or not client_secret:
        return HTMLResponse("<h1>认证失败：服务器未配置Bangumi App ID或App Secret。</h1>", status_code=500)

    token_data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": str(request.url_for('bangumi_auth_callback')),
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://bgm.tv/oauth/access_token", data=token_data)
            response.raise_for_status()
            token_info = BangumiTokenResponse.model_validate(response.json())

            # 获取用户信息
            user_resp = await client.get("https://api.bgm.tv/v0/me", headers={"Authorization": f"Bearer {token_info.access_token}"})
            user_resp.raise_for_status()
            bgm_user = BangumiUser.model_validate(user_resp.json())

            # 保存授权信息
            auth_to_save = {
                "bangumi_user_id": bgm_user.id,
                "nickname": bgm_user.nickname,
                "avatar_url": bgm_user.avatar.get("large"),
                "access_token": token_info.access_token,
                "refresh_token": token_info.refresh_token,
                "expires_at": datetime.now() + timedelta(seconds=token_info.expires_in)
            }
            await crud.save_bangumi_auth(session, user.id, auth_to_save)

        # 返回一个HTML页面，该页面将关闭自身并通知父窗口
        return HTMLResponse("<script>window.opener.postMessage('BANGUMI-OAUTH-COMPLETE', '*'); window.close();</script>")
    except Exception as e:
        logger.error(f"Bangumi OAuth 回调处理失败: {e}", exc_info=True)
        return HTMLResponse(f"<h1>认证失败: {e}</h1>", status_code=500)

@router.get("/auth/state", response_model=BangumiAuthState, summary="获取 Bangumi 授权状态")
async def get_bangumi_auth_state(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    auth_info = await crud.get_bangumi_auth(session, current_user.id)
    if not auth_info:
        return BangumiAuthState(is_authenticated=False)
    return BangumiAuthState(
        is_authenticated=True,
        nickname=auth_info.get("nickname"),
        avatar_url=auth_info.get("avatar_url"),
        bangumi_user_id=auth_info.get("bangumi_user_id"),
        authorized_at=auth_info.get("authorized_at"),
        expires_at=auth_info.get("expires_at")
    )

@router.delete("/auth", status_code=status.HTTP_204_NO_CONTENT, summary="注销 Bangumi 授权")
async def deauthorize_bangumi(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    await crud.delete_bangumi_auth(session, current_user.id)

@router.get("/search", response_model=List[BangumiApiSearchResult], summary="搜索 Bangumi 作品")
async def search_bangumi_subjects(
    keyword: str = Query(..., min_length=1),
    client: httpx.AsyncClient = Depends(get_bangumi_client),
    session: AsyncSession = Depends(get_db_session),
):
    cache_key = f"bgm_search_{keyword}"
    cached_results = await crud.get_cache(session, cache_key)
    if cached_results is not None:
        logger.info(f"Bangumi 搜索 '{keyword}' 命中缓存。")
        # The response model is List[Dict[str, Any]], so no strict validation is needed here
        # for the cached data, which is good.
        return cached_results

    async with client:
        # 步骤 1: 初始搜索以获取ID列表
        search_payload = {
            "keyword": keyword,
            "filter": {"type": [2]}  # 2: 动画 (Anime)
        }
        search_response = await client.post("https://api.bgm.tv/v0/search/subjects", json=search_payload)

        if search_response.status_code == 404:
            return []
        search_response.raise_for_status()
        
        search_result = BangumiSearchResponse.model_validate(search_response.json())
        if not search_result.data:
            return []

        # 步骤 2: 为每个搜索结果并发获取完整详情
        async def fetch_subject_details(subject_id: int, client: httpx.AsyncClient):
            try:
                # 获取完整详情以包含 infobox
                details_url = f"https://api.bgm.tv/v0/subjects/{subject_id}"
                details_response = await client.get(details_url)
                if details_response.status_code == 200:
                    return details_response.json()
            except Exception as e:
                logger.error(f"获取 Bangumi subject {subject_id} 详情失败: {e}")
            return None

        tasks = [fetch_subject_details(subject.id, client) for subject in search_result.data]
        detailed_results = await asyncio.gather(*tasks)

        # 步骤 3: 组合并格式化最终结果
        final_results = []
        for subject_data in detailed_results:
            if subject_data:
                try:
                    subject = BangumiSearchSubject.model_validate(subject_data)
                    final_results.append(BangumiApiSearchResult(
                        id=str(subject.id),
                        title=subject.display_name,
                        imageUrl=subject.image_url,
                        details=subject.details_string,
                    ))
                except ValidationError as e:
                    logger.error(f"验证 Bangumi subject 详情失败: {e}")
        
        # 缓存结果
        ttl_seconds_str = await crud.get_config_value(session, 'metadata_search_ttl_seconds', '1800')
        results_to_cache = [r.model_dump() for r in final_results]
        await crud.set_cache(session, cache_key, results_to_cache, int(ttl_seconds_str), provider='bangumi')

        return final_results

async def search_bangumi_aliases(keyword: str, client: httpx.AsyncClient) -> Set[str]:
    """从Bangumi获取别名。"""
    local_aliases: Set[str] = set()
    try:
        search_payload = {"keyword": keyword, "filter": {"type": [2]}}
        search_response = await client.post("https://api.bgm.tv/v0/search/subjects", json=search_payload)
        if search_response.status_code != 200:
            return set()

        search_result = BangumiSearchResponse.model_validate(search_response.json())
        if not search_result.data:
            return set()

        best_match = search_result.data[0]
        details_response = await client.get(f"https://api.bgm.tv/v0/subjects/{best_match.id}")
        if details_response.status_code != 200:
            return set()

        details = details_response.json()
        local_aliases.add(details.get('name'))
        local_aliases.add(details.get('name_cn'))
        for item in details.get('infobox', []):
            if item.get('key') == '别名':
                if isinstance(item['value'], str):
                    local_aliases.add(item['value'])
                elif isinstance(item['value'], list):
                    for v_item in item['value']:
                        if isinstance(v_item, dict) and v_item.get('v'):
                            local_aliases.add(v_item['v'])
        logger.info(f"Bangumi辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
    except Exception as e:
        logger.warning(f"Bangumi辅助搜索失败: {e}")
    return {alias for alias in local_aliases if alias}

async def search_bangumi_aliases(keyword: str, client: httpx.AsyncClient) -> Set[str]:
    """从Bangumi获取别名。"""
    local_aliases: Set[str] = set()
    try:
        search_payload = {"keyword": keyword, "filter": {"type": [2]}}
        search_response = await client.post("https://api.bgm.tv/v0/search/subjects", json=search_payload)
        if search_response.status_code != 200:
            return set()

        search_result = BangumiSearchResponse.model_validate(search_response.json())
        if not search_result.data:
            return set()

        best_match = search_result.data[0]
        details_response = await client.get(f"https://api.bgm.tv/v0/subjects/{best_match.id}")
        if details_response.status_code != 200:
            return set()

        details = details_response.json()
        local_aliases.add(details.get('name'))
        local_aliases.add(details.get('name_cn'))
        for item in details.get('infobox', []):
            if item.get('key') == '别名':
                if isinstance(item['value'], str):
                    local_aliases.add(item['value'])
                elif isinstance(item['value'], list):
                    for v_item in item['value']:
                        if isinstance(v_item, dict) and v_item.get('v'):
                            local_aliases.add(v_item['v'])
        logger.info(f"Bangumi辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
    except Exception as e:
        logger.warning(f"Bangumi辅助搜索失败: {e}")
    return {alias for alias in local_aliases if alias}
