"""
缓存管理 API 端点
提供缓存的查询、查看详情、删除、清除等管理操作。
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src import security
from src.db import models
from src.utils.cache_listing import count_region_keys, list_cache_page

logger = logging.getLogger(__name__)

router = APIRouter()


async def _build_cache_preview(backend, region: str, key: str) -> dict:
    """读取单个预览；由调用方有限并发执行，避免逐项串行等待。"""
    try:
        raw_value = await backend.get(key, region=region)
        if raw_value is None:
            preview = ""
        else:
            text = json.dumps(raw_value, ensure_ascii=False) if isinstance(raw_value, (dict, list)) else str(raw_value)
            preview = text[:200] + ("..." if len(text) > 200 else "")
    except Exception as exc:
        logger.warning(
            "读取缓存预览失败: region=%s key=%s error=%s",
            region, key, exc,
        )
        preview = "<读取失败>"
    return {"region": region, "key": key, "value_preview": preview}


@router.get("/cache/stats", summary="获取缓存统计信息")
async def get_cache_stats(
    current_user: models.User = Depends(security.get_current_user),
):
    """获取缓存的统计信息，包括各 region 的条目数量。"""
    from src.core.cache import get_cache_backend
    backend = get_cache_backend()

    regions = ["default", "search", "metadata", "episodes", "comments"]
    results = await asyncio.gather(
        *(count_region_keys(backend, "*", region) for region in regions),
        return_exceptions=True,
    )
    failed_regions = []
    for region, result in zip(regions, results):
        if isinstance(result, BaseException):
            failed_regions.append(region)
            logger.warning(
                "统计缓存区域失败: region=%s error=%s",
                region, result,
            )
    stats = {
        region: count for region, count in zip(regions, results)
        if isinstance(count, int) and count > 0
    }
    if len(failed_regions) == len(regions):
        raise HTTPException(status_code=503, detail="缓存后端暂时不可用")
    return {
        "total": sum(stats.values()),
        "regions": stats,
        "failedRegions": failed_regions,
    }


@router.get("/cache/list", summary="获取缓存条目列表")
async def get_cache_list(
    region: str = Query("all", description="缓存区域，all 表示全部"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: models.User = Depends(security.get_current_user),
):
    """获取指定 region 下的缓存条目列表，包含键值预览。"""
    from src.core.cache import get_cache_backend
    backend = get_cache_backend()

    pattern = f"*{search}*" if search else "*"

    # 如果是 all，遍历所有已知 region
    all_regions = ["default", "search", "metadata", "episodes", "comments"]
    regions_to_query = all_regions if region == "all" else [region]

    start = (page - 1) * pageSize
    try:
        total, paged_items = await list_cache_page(
            backend, regions_to_query, pattern, start, pageSize
        )
    except Exception as exc:
        logger.warning(
            "列出缓存失败: regions=%s pattern=%s error=%s",
            regions_to_query, pattern, exc,
        )
        raise HTTPException(status_code=503, detail="读取缓存列表失败") from exc

    # why: 页面最多读取100项，分批并发避免数据库/Redis连接被瞬间打满。
    items = []
    for index in range(0, len(paged_items), 10):
        batch = paged_items[index:index + 10]
        items.extend(await asyncio.gather(
            *(_build_cache_preview(backend, item_region, key) for item_region, key in batch)
        ))

    return {"total": total, "page": page, "pageSize": pageSize, "region": region, "items": items}


@router.delete("/cache/clear", summary="清除缓存")
async def clear_cache(
    region: Optional[str] = Query(None, description="要清除的区域，不传则清除全部"),
    current_user: models.User = Depends(security.get_current_user),
):
    """清除指定区域或全部缓存。"""
    from src.core.cache import get_cache_backend
    backend = get_cache_backend()

    count = await backend.clear(region=region)
    scope = f"区域 '{region}'" if region else "全部"
    logger.info(f"用户 '{current_user.username}' 清除了{scope}缓存，共 {count} 条")
    return {"success": True, "cleared": count, "scope": scope}


@router.delete("/cache/key", summary="删除单条缓存")
async def delete_cache_key(
    key: str = Query(..., description="缓存 key"),
    region: str = Query("search", description="缓存区域"),
    current_user: models.User = Depends(security.get_current_user),
):
    """删除指定的单条缓存。"""
    from src.core.cache import get_cache_backend
    backend = get_cache_backend()

    deleted = await backend.delete(key, region=region)
    if deleted:
        logger.info(f"用户 '{current_user.username}' 删除了缓存 key='{key}' region='{region}'")
    return {"success": deleted, "key": key, "region": region}


@router.get("/cache/detail", summary="获取单条缓存完整值")
async def get_cache_detail(
    key: str = Query(..., description="缓存 key"),
    region: str = Query("search", description="缓存区域"),
    current_user: models.User = Depends(security.get_current_user),
):
    """获取指定缓存条目的完整值，用于调试和查看详情。"""
    from src.core.cache import get_cache_backend
    backend = get_cache_backend()

    raw_value = await backend.get(key, region=region)
    if raw_value is None:
        raise HTTPException(status_code=404, detail=f"缓存 key='{key}' region='{region}' 不存在或已过期")

    # 计算值的大小（近似）
    if isinstance(raw_value, (dict, list)):
        value_str = json.dumps(raw_value, ensure_ascii=False)
    else:
        value_str = str(raw_value)

    value_type = type(raw_value).__name__
    if isinstance(raw_value, list):
        item_count = len(raw_value)
    elif isinstance(raw_value, dict):
        item_count = len(raw_value)
    else:
        item_count = None

    return {
        "key": key,
        "region": region,
        "value": raw_value,
        "value_type": value_type,
        "size_bytes": len(value_str.encode("utf-8")),
        "item_count": item_count,
    }
