"""缓存管理页的有界键列表工具。"""

from typing import List, Tuple

from src.core.cache import DatabaseBackend, HybridBackend, MemoryBackend, RedisBackend


def _strip_prefix(raw_key, prefix: str) -> str:
    key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
    return key[len(prefix):] if key.startswith(prefix) else key


async def count_region_keys(backend, pattern: str, region: str) -> int:
    """统计区域键数；数据库执行 COUNT，Redis 使用非阻塞 SCAN。"""
    if isinstance(backend, HybridBackend):
        backend = backend._database
    if isinstance(backend, DatabaseBackend):
        from src.db import crud
        async with backend._session_factory() as session:
            return await crud.count_cache_keys_by_pattern(
                session, backend._make_key(region, pattern)
            )
    if isinstance(backend, RedisBackend):
        client = await backend._get_client()
        count = 0
        async for _ in client.scan_iter(match=backend._make_key(region, pattern), count=200):
            count += 1
        return count
    return len(await backend.keys(pattern, region=region))


async def list_region_keys(
    backend, pattern: str, region: str, offset: int, limit: int
) -> List[str]:
    """分页返回区域键；Redis 使用 SCAN 顺序以保持常量级额外内存。"""
    if limit <= 0:
        return []
    if isinstance(backend, HybridBackend):
        backend = backend._database
    if isinstance(backend, DatabaseBackend):
        from src.db import crud
        prefix = f"{region}:"
        async with backend._session_factory() as session:
            keys = await crud.list_cache_keys_by_pattern(
                session, backend._make_key(region, pattern), offset, limit
            )
        return [_strip_prefix(key, prefix) for key in keys]
    if isinstance(backend, RedisBackend):
        client = await backend._get_client()
        prefix = f"{region}:"
        selected: List[str] = []
        seen = 0
        async for raw_key in client.scan_iter(
            match=backend._make_key(region, pattern), count=200
        ):
            if seen < offset:
                seen += 1
                continue
            selected.append(_strip_prefix(raw_key, prefix))
            if len(selected) >= limit:
                break
        return selected
    keys = sorted(await backend.keys(pattern, region=region))
    return keys[offset:offset + limit]


async def list_cache_page(
    backend, regions: List[str], pattern: str, offset: int, limit: int
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
