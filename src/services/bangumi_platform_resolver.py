"""
bangumi-data 平台 id → 弹幕源 scraper mediaId 转换器

把 bangumi-data sites 里的平台 id 转换成各弹幕源 get_episodes 接受的标准 mediaId。
转换规则均经实测验证（见项目记忆「bangumi-data 反向解析对接弹幕源」）：
- 腾讯 qq:   z/xxx、m/xxx → 去前缀取 cid
- 优酷 youku: showid → 直接使用（零转换）
- mgtv:      纯数字 cid → 直接使用（零转换）
- bilibili:  md 号 → 调 B 站 API 转 season_id → 拼 ss{id}
- iqiyi:     a_xxx → 抓 a_ 页面提取 v_ 链接 → 去 v_ 前缀得 link_id

返回 (provider_name, media_id)；无法转换返回 None（调用方降级别名搜索）。
注意：bilibili/iqiyi 需网络请求，调用方应并行执行并容错。
"""
import logging
import re
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# bangumi-data site 标识 → 项目 scraper provider_name
SITE_TO_PROVIDER = {
    "qq": "tencent",
    "youku": "youku",
    "mgtv": "mgtv",
    "bilibili": "bilibili",
    "bilibili_hk_mo_tw": "bilibili",
    "bilibili_hk_mo": "bilibili",
    "bilibili_tw": "bilibili",
    "iqiyi": "iqiyi",
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _tencent_cid(raw_id: str) -> Optional[str]:
    """腾讯：z/zgi40gqg05wouh9、m/mzc00200rrtzb8h → 去前缀取 cid。"""
    if not raw_id:
        return None
    return raw_id.split("/")[-1] or None


async def _bilibili_md_to_ss(md_id: str, client_factory=None) -> Optional[str]:
    """bilibili：md 号 → 调公开 API 取 season_id → 拼成 ss{season_id}。"""
    if not md_id:
        return None
    url = f"https://api.bilibili.com/pgc/review/user?media_id={md_id}"
    headers = {**_HEADERS, "Referer": "https://www.bilibili.com/"}
    try:
        if client_factory is not None:
            client = await client_factory(timeout=15.0)
        else:
            client = httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True)
        async with client:
            resp = await client.get(url, headers=headers)
            data = resp.json()
        if data.get("code") == 0:
            ss = data.get("result", {}).get("media", {}).get("season_id")
            if ss:
                return f"ss{ss}"
    except Exception as e:
        logger.warning(f"bangumi-data: bilibili md→ss 转换失败 (md={md_id}): {type(e).__name__}: {e}")
    return None


async def _iqiyi_a_to_linkid(a_id: str, client_factory=None) -> Optional[str]:
    """iqiyi：a_xxx → 抓 a_ 页面提取首个 v_ 链接 → 去 v_ 前缀得 link_id。"""
    if not a_id:
        return None
    page_url = f"https://www.iqiyi.com/{a_id}.html"
    try:
        if client_factory is not None:
            client = await client_factory(timeout=20.0)
        else:
            client = httpx.AsyncClient(timeout=20.0, headers=_HEADERS, follow_redirects=True)
        async with client:
            resp = await client.get(page_url, headers=_HEADERS)
            if resp.status_code != 200:
                return None
            v_links = re.findall(r'(v_[0-9a-z]+)\.html', resp.text)
        if v_links:
            return v_links[0][2:]  # 去掉 v_ 前缀 = scraper 的 link_id
    except Exception as e:
        logger.warning(f"bangumi-data: iqiyi a_→link_id 转换失败 (a={a_id}): {type(e).__name__}: {e}")
    return None


async def resolve_media_id(site: str, raw_id: str, client_factory=None) -> Optional[Tuple[str, str]]:
    """把 bangumi-data 的 (site, raw_id) 转换成 (provider_name, media_id)。

    :param site: bangumi-data 站点标识（qq/youku/mgtv/bilibili/iqiyi...）
    :param raw_id: bangumi-data sites 里的原始 id
    :param client_factory: 可选，scraper 的 _create_client（带代理）；缺省用裸 httpx
    :return: (provider, media_id) 或 None（无法转换 → 调用方降级别名搜索）
    """
    provider = SITE_TO_PROVIDER.get(site)
    if not provider or not raw_id:
        return None

    if provider == "tencent":
        cid = _tencent_cid(raw_id)
        return (provider, cid) if cid else None
    if provider in ("youku", "mgtv"):
        # 优酷 show_id / 芒果 cid 直接就是 scraper mediaId（零转换）
        return (provider, raw_id)
    if provider == "bilibili":
        ss = await _bilibili_md_to_ss(raw_id, client_factory)
        return (provider, ss) if ss else None
    if provider == "iqiyi":
        link_id = await _iqiyi_a_to_linkid(raw_id, client_factory)
        return (provider, link_id) if link_id else None
    return None
