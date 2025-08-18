import logging
import re
import json
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
import aiomysql
from .. import crud, models, security
from ..database import get_db_pool

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_imdb_client(
    current_user: models.User = Depends(security.get_current_user),
    pool: aiomysql.Pool = Depends(get_db_pool),
) -> httpx.AsyncClient:
    """依赖项：创建一个带有特定请求头的 httpx 客户端，以模拟浏览器访问。"""
    # --- Start of new proxy logic ---
    proxy_url_task = crud.get_config_value(pool, "proxy_url", "")
    proxy_enabled_globally_task = crud.get_config_value(pool, "proxy_enabled", "false")
    metadata_settings_task = crud.get_all_metadata_source_settings(pool)

    proxy_url, proxy_enabled_str, metadata_settings = await asyncio.gather(
        proxy_url_task, proxy_enabled_globally_task, metadata_settings_task
    )
    proxy_enabled_globally = proxy_enabled_str.lower() == 'true'

    provider_setting = next((s for s in metadata_settings if s['provider_name'] == 'imdb'), None)
    use_proxy_for_this_provider = provider_setting.get('use_proxy', False) if provider_setting else False

    proxy_to_use = proxy_url if proxy_enabled_globally and use_proxy_for_this_provider and proxy_url else None
    # --- End of new proxy logic ---
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7", # 优先请求英文内容以获得更规范的数据
    }
    return httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True, proxy=proxy_to_use)


class ImdbSearchResult(BaseModel):
    id: str
    title: str
    details: str
    image_url: Optional[str] = None


async def _search_imdb_api(keyword: str, client: httpx.AsyncClient) -> List[ImdbSearchResult]:
    """
    优化：使用IMDb的JSON API进行搜索，而不是抓取HTML页面。
    这更快速、更稳定。
    """
    # 格式化关键词以适应API的路径要求
    formatted_keyword = keyword.strip().lower()
    if not formatted_keyword:
        return []
    
    # API端点，例如: https://v3.sg.media-imdb.com/suggestion/titles/x/inception.json
    search_url = f"https://v3.sg.media-imdb.com/suggestion/titles/x/{formatted_keyword}.json"
    try:
        response = await client.get(search_url)
        response.raise_for_status()
        data = response.json()

        results = []
        # API返回的数据在 'd' 键下
        for item in data.get("d", []):
            # 过滤掉非影视剧的结果
            if item.get("q") not in ["feature", "tvSeries", "tvMovie", "tvMiniSeries", "video", "tvSpecial"]:
                continue
            
            details_parts = []
            if item.get("y"):
                details_parts.append(f"年份: {item.get('y')}")
            if item.get("s"):
                details_parts.append(f"演员: {item.get('s')}")
            
            results.append(ImdbSearchResult(
                    id=item.get("id"),
                    title=item.get("l"),
                    details=" / ".join(details_parts),
                    image_url=item.get("i", {}).get("imageUrl")
                )
            )
        return results
    except Exception as e:
        logger.error(f"IMDb API 搜索失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="IMDb API 搜索失败。")


@router.get("/search", response_model=List[ImdbSearchResult], summary="搜索 IMDb 作品")
async def search_imdb(
    keyword: str = Query(..., min_length=1),
    client: httpx.AsyncClient = Depends(get_imdb_client),
):
    """通过关键词在 IMDb 网站上搜索影视作品。"""
    return await _search_imdb_api(keyword, client)


async def _scrape_imdb_details(imdb_id: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """从 IMDb 详情页抓取作品信息。"""
    details_url = f"https://www.imdb.com/title/{imdb_id}/"
    try:
        response = await client.get(details_url)
        response.raise_for_status()
        html = response.text

        # 优化：优先从页面的 __NEXT_DATA__ JSON块中解析数据，这比正则匹配HTML更可靠
        next_data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
        if next_data_match:
            try:
                next_data = json.loads(next_data_match.group(1))
                main_data = next_data.get("props", {}).get("pageProps", {}).get("mainColumnData", {})
                
                name_en = main_data.get("titleText", {}).get("text")
                original_title = main_data.get("originalTitleText", {}).get("text")
                
                # 提取别名
                aliases_cn = []
                akas = main_data.get("akas", {})
                if akas and akas.get("edges"):
                    for edge in akas["edges"]:
                        node = edge.get("node", {})
                        if node.get("text"):
                            aliases_cn.append(node["text"])
                
                # 如果主标题不是英文，则将原始标题（通常是英文）也加入别名
                if name_en != original_title:
                    aliases_cn.append(original_title)

                return {
                    "id": imdb_id, "imdb_id": imdb_id,
                    "name_en": name_en, "name_jp": None,
                    "aliases_cn": list(dict.fromkeys(filter(None, aliases_cn))) # 去重并移除空值
                }
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"解析 IMDb __NEXT_DATA__ 失败，将回退到正则匹配。错误: {e}")

        # --- Fallback to Regex if __NEXT_DATA__ fails ---
        title_match = re.search(r'<h1.*?><span.*?>(.*?)</span></h1>', html)
        name_en = title_match.group(1).strip() if title_match else None
        akas_section_match = re.search(r'<div data-testid="akas".*?>(.*?)</div>', html, re.DOTALL)
        aliases_cn = []
        if akas_section_match:
            alias_matches = re.findall(r'<li.*?<a.*?>(.*?)</a>', akas_section_match.group(1), re.DOTALL)
            aliases_cn = [alias.strip() for alias in alias_matches]

        return {
            "id": imdb_id, "imdb_id": imdb_id,
            "name_en": name_en, "name_jp": None,
            "aliases_cn": list(dict.fromkeys(filter(None, aliases_cn))),
        }

    except Exception as e:
        logger.error(f"解析 IMDb 详情页时发生错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="解析 IMDb 详情页失败。")


@router.get("/details/{imdb_id}", response_model=Dict[str, Any], summary="获取 IMDb 作品详情")
async def get_imdb_details(
    imdb_id: str = Path(...), client: httpx.AsyncClient = Depends(get_imdb_client)
):
    """获取指定 IMDb ID 的作品详情，主要用于提取别名。"""
    return await _scrape_imdb_details(imdb_id, client)