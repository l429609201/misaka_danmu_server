"""
通用订阅 API。

设计依据：docs/subscription_page_implementation_plan.md 第 5 节。
核心约束：
- 接口全部通用，使用 provider/type/payload，不出现 provider 专用路由。
- 「是否能作为订阅源」由源自己声明（supports_subscription）与自检
  （check_subscription_capability）决定，API 只负责聚合与读写。
- 数据统一落 external_calendar_item，不新增专表。
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db_session
from src.db.crud import external_calendar as ext_cal_crud
from src import security
from src.db import models
from src.api.dependencies import get_metadata_manager, get_scraper_manager
from src.services import MetadataSourceManager, ScraperManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


# ============ 请求模型 ============

class CreateTargetRequest(BaseModel):
    """创建订阅目标：provider + type + payload 通用结构。"""
    provider: str
    type: str
    payload: Dict[str, Any] = {}
    runNow: bool = False


class UpdateTargetRequest(BaseModel):
    """修改订阅目标：启用状态 / 状态 / 通用 extraData 补丁。"""
    enabled: Optional[bool] = None
    status: Optional[str] = None
    extraPatch: Optional[Dict[str, Any]] = None


# ============ 能力探测辅助 ============

async def _probe_source(provider: str, source: Any, source_type: str, user: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """对单个源做订阅能力探测，返回标准条目；不支持订阅则返回 None。

    source_type: 'danmaku'（弹幕源）| 'metadata'（元数据源）。
    user: 当前请求用户，OAuth 类源用它判断授权状态。
    """
    if not getattr(source, "supports_subscription", False):
        return None
    try:
        capability = await source.check_subscription_capability(user=user)
    except TypeError:
        # 兼容旧实现（不接受 user 参数）
        capability = await source.check_subscription_capability()
    except Exception as e:
        logger.warning(f"探测订阅源 '{provider}' 能力失败: {e}")
        capability = {
            "available": False,
            "authRequired": True,
            "authStatus": "unknown",
            "reason": f"能力检测异常: {e}",
            "subscriptionTypes": [],
        }
    display_name = getattr(source, "display_name", None) or provider
    sub_types = capability.get("subscriptionTypes") or getattr(source, "subscription_types", [])
    return {
        "provider": provider,
        "displayName": display_name,
        "sourceType": source_type,
        "available": bool(capability.get("available", False)),
        "authRequired": bool(capability.get("authRequired", False)),
        "authStatus": capability.get("authStatus", "unknown"),
        "features": [t.get("type") for t in sub_types if isinstance(t, dict) and t.get("type")],
        "subscriptionTypes": sub_types,
        # 供前端 URL 订阅做域名粗校验（如 bilibili 的 www.bilibili.com / b23.tv）
        "handledDomains": list(getattr(source, "handled_domains", []) or []),
        "reason": capability.get("reason"),
    }


@router.get("/available-sources", summary="探测当前可用订阅源")
async def get_available_sources(
    user: models.User = Depends(security.get_current_user),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
):
    """聚合弹幕源 / 元数据源中声明并自检通过的订阅源。

    扫描规则（与文档 5.4 一致）：
      源已加载 AND 源已启用 AND supports_subscription=True AND check 通过。
    前端只渲染 available=true 的入口，available=false 仅进「配置提示」。
    """
    danmaku_sources: List[Dict[str, Any]] = []
    calendar_sources: List[Dict[str, Any]] = []

    # 弹幕源：走 ScraperManager
    for provider, scraper in scraper_manager.scrapers.items():
        setting = scraper_manager.scraper_settings.get(provider, {})
        if not setting.get("isEnabled", True):
            continue
        entry = await _probe_source(provider, scraper, "danmaku", user=user)
        if entry is not None:
            danmaku_sources.append(entry)

    # 元数据源：走 MetadataSourceManager
    for provider, source in metadata_manager.sources.items():
        setting = metadata_manager.source_settings.get(provider, {})
        if not setting.get("isEnabled", True):
            continue
        entry = await _probe_source(provider, source, "metadata", user=user)
        if entry is not None:
            calendar_sources.append(entry)

    all_entries = danmaku_sources + calendar_sources
    available_count = sum(1 for e in all_entries if e["available"])
    return {
        "danmakuSources": danmaku_sources,
        "calendarSources": calendar_sources,
        "summary": {
            "availableCount": available_count,
            "unavailableCount": len(all_entries) - available_count,
        },
    }


# ============ 订阅源实例解析 ============

def _resolve_source(
    provider: str,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataSourceManager,
) -> Optional[Any]:
    """按 provider 找出对应的源实例（优先弹幕源，再元数据源）。"""
    source = scraper_manager.scrapers.get(provider)
    if source is not None:
        return source
    return metadata_manager.sources.get(provider)


# ============ 订阅目标 CRUD ============

@router.get("/discover", summary="发现可订阅目标")
async def discover_targets(
    provider: str = Query(..., description="订阅源标识，如 bilibili"),
    query: str = Query(..., description="关键词或视频/合集 URL"),
    type: Optional[str] = Query(None, description="可选：限制订阅类型（如 bilibili_up / bilibili_bangumi）"),
    user: models.User = Depends(security.get_current_user),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
):
    """通用入口：调用源 discover_subscription_targets，返回候选列表供前端挑选。

    返回项形如：{type, title, cover, description, payload}；payload 用于后续 createTarget。
    """
    source = _resolve_source(provider, scraper_manager, metadata_manager)
    if source is None or not getattr(source, "supports_subscription", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"订阅源 '{provider}' 未加载或不支持订阅",
        )
    try:
        try:
            items = await source.discover_subscription_targets(query, type or "", user=user)
        except TypeError:
            # 兼容旧签名（不接受 user）
            items = await source.discover_subscription_targets(query, type or "")
    except NotImplementedError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{provider}' 未实现 discover")
    except Exception as e:
        logger.error(f"discover 调用失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"discover 失败: {e}")
    return {"list": items or []}


@router.get("/discover/offline", summary="离线探索（bangumi-data 为主 + 在线为辅）")
async def discover_offline(
    query: str = Query(..., description="关键词（支持任意语言译名）"),
    onlineProvider: Optional[str] = Query(None, description="可选：辅助在线探索源，如 bangumi / trakt"),
    user: models.User = Depends(security.get_current_user),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
):
    """以 bangumi-data 离线库为主探索源（秒搜+多语言+带平台映射），在线 API 为辅。

    - 主：查本地 bangumi_data_index，返回 bangumi_data_subject 候选（payload 含 sites 平台映射）。
    - 辅：若指定 onlineProvider，并行调其 discover 补充（在线源结果排在离线结果之后）。
    返回 {list:[...]}，结构与 /discover 对齐，供前端统一渲染。
    """
    from src.services import get_bangumi_data_manager
    offline_items: List[Dict[str, Any]] = []
    bgm_mgr = get_bangumi_data_manager()
    if bgm_mgr is not None:
        try:
            offline_items = await bgm_mgr.discover_offline(query)
        except Exception as e:
            logger.warning(f"bangumi-data 离线探索失败: {type(e).__name__}: {e}")

    # 辅助在线探索（可选）
    online_items: List[Dict[str, Any]] = []
    if onlineProvider:
        source = _resolve_source(onlineProvider, scraper_manager, metadata_manager)
        if source is not None and getattr(source, "supports_subscription", False):
            try:
                try:
                    online_items = await source.discover_subscription_targets(query, "", user=user)
                except TypeError:
                    online_items = await source.discover_subscription_targets(query, "")
            except (NotImplementedError, Exception) as e:
                logger.warning(f"在线辅助探索失败 ({onlineProvider}): {type(e).__name__}: {e}")

    return {"list": (offline_items or []) + (online_items or [])}

async def sync_explore(
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
):
    """汇总各订阅源的探索榜单（弹幕源 fetch_subscription_calendar）写入 external_calendar_item。

    元数据源（Bangumi/Trakt）的日历仍由 /calendar/weekly 实时聚合，这里只补充弹幕订阅源
    （如 Bilibili PGC）的探索榜单，统一落库供探索发现网格读取。
    """
    synced: Dict[str, int] = {}
    for provider, scraper in scraper_manager.scrapers.items():
        setting = scraper_manager.scraper_settings.get(provider, {})
        if not setting.get("isEnabled", True):
            continue
        if not getattr(scraper, "supports_subscription", False):
            continue
        try:
            items = await scraper.fetch_subscription_calendar()
        except NotImplementedError:
            continue
        except Exception as e:
            logger.warning(f"探索同步：源 '{provider}' 拉取失败: {e}")
            continue
        if items:
            count = await ext_cal_crud.upsert_items(session, provider, items)
            synced[provider] = count
    return {"synced": synced, "message": f"探索榜单同步完成：{synced}"}


@router.get("/explore", summary="查询探索榜单（海报网格）")
async def list_explore(
    provider: Optional[str] = Query(None, description="按源过滤，如 bilibili"),
    category: Optional[str] = Query(None, description="探索分类，如 bangumi / guochuang"),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(30, ge=1, le=100),
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """读取已落库的探索榜单条目（airWeekday 为空的外部条目），供探索发现网格分页展示。"""
    return await ext_cal_crud.list_explore_items(
        session, provider=provider, category=category,
        keyword=keyword, page=page, page_size=pageSize,
    )


@router.get("/targets", summary="查询订阅目标")
async def list_targets(
    provider: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    status_: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """分页查询订阅目标。type 对应 extraData.subscriptionType。"""
    return await ext_cal_crud.list_subscription_targets(
        session,
        provider=provider,
        subscription_type=type,
        status=status_,
        keyword=keyword,
        page=page,
        page_size=pageSize,
    )


@router.post("/targets", summary="创建订阅目标", status_code=status.HTTP_201_CREATED)
async def create_target(
    body: CreateTargetRequest,
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
):
    """创建订阅目标：由对应源 validate_subscription_payload 标准化后写库。"""
    source = _resolve_source(body.provider, scraper_manager, metadata_manager)
    if source is None or not getattr(source, "supports_subscription", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"订阅源 '{body.provider}' 未加载或不支持订阅",
        )
    try:
        normalized = await source.validate_subscription_payload(body.type, body.payload)
    except NotImplementedError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{body.provider}' 未实现该订阅类型")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"校验订阅参数失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"校验订阅参数失败: {e}")

    target = await ext_cal_crud.upsert_subscription_target(
        session,
        provider=normalized["provider"],
        external_id=normalized["externalId"],
        title=normalized.get("title") or "",
        subscription_type=normalized.get("subscriptionType") or body.type,
        extra={**(normalized.get("extraData") or {}), "animeType": normalized.get("animeType", "subscription")},
        status="pending",
    )
    return {
        "id": target.get("id"),
        "provider": target.get("provider"),
        "externalId": target.get("externalId"),
        "type": target.get("subscriptionType"),
        "title": target.get("animeTitle"),
        "status": target.get("subscriptionStatus"),
        "message": "订阅目标已创建",
    }


@router.patch("/targets/{target_id}", summary="修改订阅目标")
async def update_target(
    target_id: int,
    body: UpdateTargetRequest,
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """修改启用状态 / 状态 / 通用 extraData 字段。"""
    target = await ext_cal_crud.get_by_id(session, target_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订阅目标不存在")
    ok = await ext_cal_crud.update_subscription_target(
        session,
        provider=target["provider"],
        external_id=target["externalId"],
        enabled=body.enabled,
        extra_patch=body.extraPatch,
        status=body.status,
    )
    return {"success": ok}


@router.delete("/targets/{target_id}", summary="取消订阅目标")
async def delete_target(
    target_id: int,
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """取消订阅（重置订阅意向字段）。"""
    target = await ext_cal_crud.get_by_id(session, target_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订阅目标不存在")
    ok = await ext_cal_crud.unsubscribe(session, target["provider"], target["externalId"])
    return {"success": ok}


@router.post("/targets/{target_id}/scan", summary="立即扫描订阅目标")
async def scan_target(
    target_id: int,
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
):
    """立即扫描一个订阅目标，把发现的候选项写入 external_calendar_item。

    扫描逻辑由对应源的 scan_subscription_target 提供；写库统一走 CRUD。
    """
    target = await ext_cal_crud.get_by_id(session, target_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订阅目标不存在")

    source = _resolve_source(target["provider"], scraper_manager, metadata_manager)
    if source is None or not getattr(source, "supports_subscription", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"订阅源 '{target['provider']}' 未加载或不支持订阅",
        )

    try:
        items = await source.scan_subscription_target(target)
    except NotImplementedError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{target['provider']}' 未实现扫描能力")
    except Exception as e:
        logger.error(f"扫描订阅目标失败: {e}", exc_info=True)
        await ext_cal_crud.update_subscription_next_scan(
            session, target["provider"], target["externalId"], last_error=str(e)
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"扫描失败: {e}")

    written = 0
    for item in items or []:
        await ext_cal_crud.upsert_subscription_item(
            session,
            provider=item.get("provider", target["provider"]),
            external_id=item["externalId"],
            title=item.get("title") or "",
            subscription_type=item.get("subscriptionType") or "",
            parent_external_id=item.get("extraData", {}).get("parentExternalId") or target["externalId"],
            extra={k: v for k, v in (item.get("extraData") or {}).items()},
            status=item.get("status", "waiting"),
        )
        written += 1

    await ext_cal_crud.update_subscription_next_scan(session, target["provider"], target["externalId"])
    return {"scanned": written, "message": f"扫描完成，写入 {written} 个候选项"}


# ============ 订阅候选项 ============

@router.get("/items", summary="查询订阅候选项")
async def list_items(
    provider: Optional[str] = Query(None),
    parentExternalId: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    status_: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """分页查询订阅目标产生的候选项（视频候选 / 分集候选）。"""
    return await ext_cal_crud.list_subscription_items(
        session,
        parent_external_id=parentExternalId,
        provider=provider,
        subscription_type=type,
        status=status_,
        keyword=keyword,
        page=page,
        page_size=pageSize,
    )


@router.post("/items/{item_id}/retry", summary="重试订阅候选项")
async def retry_item(
    item_id: int,
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """把候选项重置为 waiting，等待下一轮扫描重新处理。"""
    item = await ext_cal_crud.get_by_id(session, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    ok = await ext_cal_crud.retry_subscription_item(session, item["provider"], item["externalId"])
    return {"success": ok}


@router.post("/items/{item_id}/ignore", summary="忽略订阅候选项")
async def ignore_item(
    item_id: int,
    user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """标记候选项为 ignored。"""
    item = await ext_cal_crud.get_by_id(session, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    ok = await ext_cal_crud.ignore_subscription_item(session, item["provider"], item["externalId"])
    return {"success": ok}


class ResolveUrlRequest(BaseModel):
    """URL 解析请求体：把 URL 喂给所有可订阅源，由源自身按 handled_domains 判断能否处理。"""
    url: str


@router.post("/resolve-url", summary="按 URL 自动定位订阅源并发现候选")
async def resolve_url(
    body: ResolveUrlRequest,
    user: models.User = Depends(security.get_current_user),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
):
    """通用 URL 入口：
    - 遍历所有 supports_subscription=True 的源（弹幕源 + 元数据源）
    - 按源自身声明的 handled_domains 匹配
    - 命中后直接调 discover_subscription_targets(url)
    - 返回 {provider, list}；未命中返回 400
    
    这样新增/移除订阅源时前端无需任何改动。
    """
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL 不能为空")

    # 收集所有声明订阅能力的源（弹幕源 + 元数据源），保留 provider 名以便定位
    candidates = []
    for provider, scraper in scraper_manager.scrapers.items():
        setting = scraper_manager.scraper_settings.get(provider, {})
        if not setting.get("isEnabled", True):
            continue
        if not getattr(scraper, "supports_subscription", False):
            continue
        candidates.append((provider, scraper))
    for provider, source in metadata_manager.sources.items():
        setting = metadata_manager.source_settings.get(provider, {})
        if not setting.get("isEnabled", True):
            continue
        if not getattr(source, "supports_subscription", False):
            continue
        candidates.append((provider, source))

    # 按 handled_domains 匹配（源自行声明）
    matched = None
    for provider, source in candidates:
        domains = list(getattr(source, "handled_domains", []) or [])
        if any(d and d in url for d in domains):
            matched = (provider, source)
            break

    if matched is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该 URL 不在任何可用订阅源的域名范围内",
        )

    provider, source = matched
    # 优先走结构化解析（当前视频/合集/合集视频三段），供前端独立弹框多选订阅
    try:
        try:
            structured = await source.resolve_url_structured(url, user=user)
        except TypeError:
            # 兼容旧签名（不接受 user）
            structured = await source.resolve_url_structured(url)
        if structured:
            return {"provider": provider, "matched": structured}
    except NotImplementedError:
        structured = None
    except Exception as e:
        logger.warning(f"resolve-url 结构化解析失败，降级 discover ({provider}): {type(e).__name__}: {e}")

    # 降级：源未实现结构化解析 → 回退通用 discover
    try:
        # 兼容旧签名（不接受 user）
        try:
            items = await source.discover_subscription_targets(url, "", user=user)
        except TypeError:
            items = await source.discover_subscription_targets(url, "")
    except NotImplementedError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{provider}' 未实现 discover")
    except Exception as e:
        logger.error(f"resolve-url discover 失败 ({provider}): {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"URL 解析失败: {e}")

    return {"provider": provider, "list": items or []}
