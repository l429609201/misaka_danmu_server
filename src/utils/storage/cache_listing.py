"""缓存管理页的键统计与分页工具，仅依赖调用方提供的公开接口。"""

from typing import List, Protocol, Tuple


class CacheKeyReader(Protocol):
    """描述键读取契约，避免工具层反向导入服务或具体数据库驱动。"""

    async def keys(self, pattern: str = "*", region: str = "default") -> List[str]:
        """返回匹配模式的业务键，不包含区域前缀。"""
        ...


async def count_region_keys(backend: CacheKeyReader, pattern: str, region: str) -> int:
    """通过公开键接口统计区域条目，后端异常交由调用方处理。"""
    return len(await backend.keys(pattern, region=region))


async def list_region_keys(
    backend: CacheKeyReader, pattern: str, region: str, offset: int, limit: int
) -> List[str]:
    """按业务键排序后分页，保持各缓存模式一致的列表顺序。"""
    if limit <= 0:
        return []
    # 驱动选择和混合模式的数据来源由 CacheService 决定，不访问其私有属性。
    keys = sorted(await backend.keys(pattern, region=region))
    return keys[offset:offset + limit]


async def list_cache_page(
    backend: CacheKeyReader, regions: List[str], pattern: str, offset: int, limit: int
) -> Tuple[int, List[Tuple[str, str]]]:
    """按 region/key 的稳定顺序拼接分页，不构建全量跨区域键列表。"""
    counts = []
    for region in regions:
        counts.append(await count_region_keys(backend, pattern, region))

    total = sum(counts)
    remaining = limit
    local_offset = offset
    page: List[Tuple[str, str]] = []
    for region, count in zip(regions, counts):
        if local_offset >= count:
            local_offset -= count
            continue
        keys = await list_region_keys(backend, pattern, region, local_offset, remaining)
        page.extend((region, key) for key in keys)
        remaining -= len(keys)
        local_offset = 0
        if remaining <= 0:
            break
    return total, page
