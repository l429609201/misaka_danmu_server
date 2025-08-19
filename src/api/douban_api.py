import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
from typing import Set
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ValidationError

from .. import crud, models, security
from ..database import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter()

async def _create_douban_client(session: AsyncSession) -> httpx.AsyncClient:
    """Creates an httpx.AsyncClient with Douban cookie and proxy settings."""
    proxy_url_task = crud.get_config_value(session, "proxy_url", "")
    proxy_enabled_globally_task = crud.get_config_value(session, "proxy_enabled", "false")
    metadata_settings_task = crud.get_all_metadata_source_settings(session)

    proxy_url, proxy_enabled_str, metadata_settings = await asyncio.gather(
        proxy_url_task, proxy_enabled_globally_task, metadata_settings_task
    )
    proxy_enabled_globally = proxy_enabled_str.lower() == 'true'

    provider_setting = next((s for s in metadata_settings if s['providerName'] == 'douban'), None)
    use_proxy_for_this_provider = provider_setting.get('use_proxy', False) if provider_setting else False

    proxies = proxy_url if proxy_enabled_globally and use_proxy_for_this_provider and proxy_url else None
    
    cookie = await crud.get_config_value(session, "douban_cookie", "")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    if cookie:
        headers["Cookie"] = cookie
    return httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True, proxy=proxies)

async def get_douban_client(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> httpx.AsyncClient:
    """依赖项：创建一个带有可选豆瓣Cookie的httpx客户端。"""
    return await _create_douban_client(session)


class DoubanSearchResult(BaseModel):
    id: str
    title: str
    details: str
    imageUrl: Optional[str] = None

# --- 新增：用于解析豆瓣JSON API响应的模型 ---
class DoubanJsonSearchSubject(BaseModel):
    id: str
    title: str
    url: str
    cover: str
    rate: str
    cover_x: int
    cover_y: int

class DoubanJsonSearchResponse(BaseModel):
    subjects: List[DoubanJsonSearchSubject]


async def _search_douban_json_api(keyword: str, client: httpx.AsyncClient) -> List[DoubanSearchResult]:
    """通过豆瓣的JSON API搜索影视作品，这比抓取HTML更稳定、更快速。"""
    # 并发请求电影和电视剧两个分类
    movie_task = client.get(
        "https://movie.douban.com/j/search_subjects",
        params={"type": "movie", "tag": keyword, "page_limit": 20, "page_start": 0}
    )
    tv_task = client.get(
        "https://movie.douban.com/j/search_subjects",
        params={"type": "tv", "tag": keyword, "page_limit": 20, "page_start": 0}
    )

    try:
        movie_res, tv_res = await asyncio.gather(movie_task, tv_task, return_exceptions=True)
        
        all_subjects = []
        for res in [movie_res, tv_res]:
            if isinstance(res, httpx.Response):
                if res.status_code == 200:
                    try:
                        data = DoubanJsonSearchResponse.model_validate(res.json())
                        all_subjects.extend(data.subjects)
                    except ValidationError as e:
                        logger.warning(f"解析豆瓣JSON API响应失败: {e} - 响应: {res.text[:200]}")
                else:
                    logger.warning(f"请求豆瓣JSON API失败，状态码: {res.status_code}")
            elif isinstance(res, Exception):
                logger.error(f"请求豆瓣JSON API时发生网络错误: {res}")

        # 去重并格式化为前端期望的 DoubanSearchResult 格式
        seen_ids = set()
        results = []
        for subject in all_subjects:
            if subject.id not in seen_ids:
                results.append(
                    DoubanSearchResult(
                        id=subject.id,
                        title=subject.title,
                        details=f"评分: {subject.rate}",
                        imageUrl=subject.cover,
                    )
                )
                seen_ids.add(subject.id)
        return results

    except Exception as e:
        logger.error(f"处理豆瓣JSON API请求时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="豆瓣搜索失败。")

@router.get("/search", response_model=List[DoubanSearchResult], summary="搜索豆瓣作品")
async def search_douban(
    keyword: str = Query(..., min_length=1),
    client: httpx.AsyncClient = Depends(get_douban_client),
):
    """通过关键词在豆瓣网站上搜索影视作品。"""
    return await _search_douban_json_api(keyword, client) # This function is already good

async def get_douban_details_logic(douban_id: str, client: httpx.AsyncClient) -> "models.MetadataDetailsResponse":
    """从豆瓣详情页抓取作品信息。"""
    details_url = f"https://movie.douban.com/subject/{douban_id}/"
    try:
        response = await client.get(details_url)
        response.raise_for_status()
        html = response.text

        # 提取标题
        title_match = re.search(r'<span property="v:itemreviewed">(.*?)</span>', html)
        title = title_match.group(1).strip() if title_match else ""

        # 提取别名
        aliases_cn = []
        alias_match = re.search(r'<span class="pl">又名:</span>(.*?)<br/>', html)
        if alias_match:
            aliases_text = alias_match.group(1)
            aliases_cn = [
                alias.strip() for alias in aliases_text.split("/") if alias.strip()
            ]

        # 提取IMDb ID
        imdb_id_match = re.search(
            r'<a href="https://www.imdb.com/title/(tt\d+)"', html
        )
        imdb_id = imdb_id_match.group(1) if imdb_id_match else None

        # 提取日文名和英文名
        name_jp_match = re.search(r'<span class="pl">片名</span>(.*?)<br/>', html)
        name_jp = name_jp_match.group(1).strip() if name_jp_match else None

        name_en_match = re.search(r'<span class="pl">官方网站:</span>.*?<a href=".*?" target="_blank" rel="nofollow">(.*?)</a>', html)
        name_en = name_en_match.group(1).strip() if name_en_match else None

        # 整合所有中文名
        if title:
            aliases_cn.insert(0, title)
        
        # 去重
        aliases_cn = list(dict.fromkeys(aliases_cn))

        return {
            "id": douban_id,
            "imdb_id": imdb_id,
            "name_en": name_en,
            "name_jp": name_jp,
            "aliases_cn": aliases_cn,
        }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            logger.error(f"豆瓣详情页请求被拒绝(403)，可能是Cookie已失效或IP被限制。ID: {douban_id}")
            raise HTTPException(status_code=403, detail="豆瓣请求被拒绝，请检查Cookie或网络环境。")
        raise HTTPException(status_code=500, detail=f"请求豆瓣详情时发生错误: {e}")
    except Exception as e:
        logger.error(f"解析豆瓣详情页时发生错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="解析豆瓣详情页失败。")

@router.get("/details/{douban_id}", response_model=models.MetadataDetailsResponse, summary="获取豆瓣作品详情")
async def get_douban_details(
    douban_id: str = Path(...), client: httpx.AsyncClient = Depends(get_douban_client)
):
    """获取指定豆瓣ID的作品详情，主要用于提取别名。"""
    return await _scrape_douban_details(douban_id, client)

async def search_douban_aliases(keyword: str, client: httpx.AsyncClient) -> Set[str]:
    """从豆瓣获取别名。"""
    local_aliases: Set[str] = set()
    try:
        movie_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "movie", "tag": keyword, "page_limit": 1, "page_start": 0})
        tv_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "tv", "tag": keyword, "page_limit": 1, "page_start": 0})
        movie_res, tv_res = await asyncio.gather(movie_task, tv_task, return_exceptions=True)

        best_subject_id = None
        if isinstance(movie_res, httpx.Response) and movie_res.status_code == 200:
            if subjects := movie_res.json().get('subjects', []):
                best_subject_id = subjects[0]['id']
        if not best_subject_id and isinstance(tv_res, httpx.Response) and tv_res.status_code == 200:
            if subjects := tv_res.json().get('subjects', []):
                best_subject_id = subjects[0]['id']

        if best_subject_id:
            details = await _scrape_douban_details(best_subject_id, client)
            local_aliases.update(details.get('aliases_cn', []))
        logger.info(f"豆瓣辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
    except Exception as e:
        logger.warning(f"豆瓣辅助搜索失败: {e}")
    return {alias for alias in local_aliases if alias}

async def search_douban_aliases(keyword: str, client: httpx.AsyncClient) -> Set[str]:
    """从豆瓣获取别名。"""
    local_aliases: Set[str] = set()
    try:
        movie_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "movie", "tag": keyword, "page_limit": 1, "page_start": 0})
        tv_task = client.get("https://movie.douban.com/j/search_subjects", params={"type": "tv", "tag": keyword, "page_limit": 1, "page_start": 0})
        movie_res, tv_res = await asyncio.gather(movie_task, tv_task, return_exceptions=True)

        best_subject_id = None
        if isinstance(movie_res, httpx.Response) and movie_res.status_code == 200:
            if subjects := movie_res.json().get('subjects', []):
                best_subject_id = subjects[0]['id']
        if not best_subject_id and isinstance(tv_res, httpx.Response) and tv_res.status_code == 200:
            if subjects := tv_res.json().get('subjects', []):
                best_subject_id = subjects[0]['id']

        if best_subject_id:
            details = await get_douban_details_logic(best_subject_id, client)
            local_aliases.update(details.aliasesCn or [])
        logger.info(f"豆瓣辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
    except Exception as e:
        logger.warning(f"豆瓣辅助搜索失败: {e}")
    return {alias for alias in local_aliases if alias}
