"""bangumi-data 平台标识到弹幕源标识的外部协议适配。"""

import logging
import re
from typing import Callable

import httpx

from src.utils.parsing.bangumi_platforms import SITE_TO_PROVIDER, map_static_media_id

logger = logging.getLogger(__name__)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


async def resolve_media_id(site: str, raw_id: str, client_factory: Callable | None = None) -> tuple[str, str] | None:
    """转换平台标识；网络转换失败返回空结果供上层退回别名搜索。"""
    static = map_static_media_id(site, raw_id)
    if static:
        return static
    provider = SITE_TO_PROVIDER.get(site)
    if not raw_id or provider not in ("bilibili", "iqiyi"):
        return None
    headers = dict(_HEADERS)
    if provider == "bilibili":
        url = f"https://api.bilibili.com/pgc/review/user?media_id={raw_id}"
        headers["Referer"] = "https://www.bilibili.com/"
    else:
        url = f"https://www.iqiyi.com/{raw_id}.html"
    try:
        client = await client_factory(timeout=20.0) if client_factory else httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        async with client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            if provider == "bilibili":
                data = response.json()
                season_id = (data.get("result") or {}).get("media", {}).get("season_id") if data.get("code") == 0 else None
                return (provider, f"ss{season_id}") if season_id else None
            links = re.findall(r'(v_[0-9a-z]+)\.html', response.text)
            return (provider, links[0][2:]) if links else None
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("bangumi-data: 平台转换失败 (%s=%s): %s", site, raw_id, exc)
        return None
