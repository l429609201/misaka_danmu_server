"""
缓存管理 API 端点
提供缓存的查询、查看详情、删除、清除等管理操作。
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src import security
from src.db import models

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/cache/stats", summary="获取缓存统计信息")
async def get_cache_stats(
    current_user: models.User = Depends(security.get_current_user),
):
    """获取缓存的统计信息，包括各 region 的条目数量。"""
    from src.core.cache import get_cache_backend
    backend = get_cache_backend()

    regions = ["default", "search", "metadata", "episodes", "comments"]
    stats = {}
    total = 0
    for region in regions:
        try:
            region_keys = await backend.keys("*", region=region)
            count = len(region_keys)
            if count > 0:
                stats[region] = count
                total += count
        except Exception:
            pass

    return {"total": total, "regions": stats}


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

    all_items = []  # [(region, key)]
    for r in regions_to_query:
        try:
            region_keys = await backend.keys(pattern, region=r)
            for k in region_keys:
                all_items.append((r, k))
        except Exception:
            pass

    all_items.sort(key=lambda x: (x[0], x[1]))

    total = len(all_items)
    start = (page - 1) * pageSize
    end = start + pageSize
    paged_items = all_items[start:end]

    # 获取键值预览
    items = []
    for r, k in paged_items:
        value_preview = ""
        try:
            raw_value = await backend.get(k, region=r)
            if raw_value is not None:
                if isinstance(raw_value, (dict, list)):
                    text = json.dumps(raw_value, ensure_ascii=False)
                else:
                    text = str(raw_value)
                # 截断预览，最多200字符
                value_preview = text[:200] + ("..." if len(text) > 200 else "")
        except Exception:
            value_preview = "<读取失败>"
        items.append({"region": r, "key": k, "value_preview": value_preview})

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
