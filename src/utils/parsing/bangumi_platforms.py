"""bangumi-data 平台标识的纯映射规则。"""

SITE_TO_PROVIDER = {
    "qq": "tencent", "youku": "youku", "mgtv": "mgtv",
    "bilibili": "bilibili", "bilibili_hk_mo_tw": "bilibili",
    "bilibili_hk_mo": "bilibili", "bilibili_tw": "bilibili", "iqiyi": "iqiyi",
}


def map_static_media_id(site: str, raw_id: str) -> tuple[str, str] | None:
    """返回无需联网的平台映射，未知平台与空标识不产生结果。"""
    provider = SITE_TO_PROVIDER.get(site)
    if not raw_id:
        return None
    if provider == "tencent":
        cid = raw_id.split("/")[-1]
        return (provider, cid) if cid else None
    if provider in ("youku", "mgtv"):
        return provider, raw_id
    return None
