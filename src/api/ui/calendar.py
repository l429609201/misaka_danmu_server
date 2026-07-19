"""
日历视图 API
"""
import logging
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from sqlalchemy import select, update as sql_update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, get_db_session, orm_models
from src import security
from src.db import models
from src import tasks
from src.api.dependencies import (
    get_metadata_manager, get_task_manager, get_scraper_manager,
    get_config_manager, get_rate_limiter, get_ai_matcher_manager,
    get_title_recognition_manager,
)
from src.services import MetadataSourceManager, ScraperManager, TaskManager
from src.db import ConfigManager
from src.rate_limiter import RateLimiter
from src.ai import AIMatcherManager
from src.api.control.models import (
    ControlAutoImportRequest, AutoImportSearchType, AutoImportMediaType,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/calendar", tags=["Calendar"])


def _normalize_calendar_title(title: Optional[str]) -> str:
    """用于本地条目与外部日历条目弱关联的标题归一化。"""
    if not title:
        return ""
    text = str(title).lower()
    text = re.sub(r"[\s\-_:：·・,，.。!！?？'\"“”‘’()[\]（）【】]+", "", text)
    text = re.sub(r"第[0-9一二三四五六七八九十]+季", "", text)
    text = re.sub(r"season[0-9]+|s[0-9]+", "", text)
    return text.strip()


def _calendar_title_key(title: Optional[str], season: Optional[int], anime_type: Optional[str]) -> Optional[tuple]:
    normalized = _normalize_calendar_title(title)
    if not normalized:
        return None
    return normalized, season or 1, anime_type or "tv_series"


def _build_source_descriptor(origin: str, cal_item: dict) -> dict:
    """构造统一的「可订阅源描述」对象，供前端多源选择弹框使用。
    字段对齐 subscribeCalendarItem 入参，前端可直接据此发起订阅。"""
    bgm_id = cal_item.get("bangumiId")
    trakt_id = cal_item.get("traktId")
    tmdb_id = cal_item.get("tmdbId") or cal_item.get("traktTmdbId")
    return {
        "origin": origin,
        "provider": cal_item.get("provider") or origin,
        "externalId": cal_item.get("externalId") or bgm_id or trakt_id or (str(tmdb_id) if tmdb_id else None),
        "animeTitle": cal_item.get("animeTitle") or cal_item.get("titleZh"),
        "titleZh": cal_item.get("titleZh"),
        "season": cal_item.get("season"),
        "mediaType": cal_item.get("animeType") or "tv_series",
        "bangumiId": bgm_id,
        "traktId": trakt_id,
        "tmdbId": str(tmdb_id) if tmdb_id else None,
        "traktTmdbId": str(tmdb_id) if tmdb_id else cal_item.get("traktTmdbId"),
        "rating": cal_item.get("rating"),
        "subscriptionType": cal_item.get("subscriptionType"),
    }


def _append_available_source(entry: dict, origin: str, cal_item: dict) -> None:
    """把一个外部源追加到 entry['availableSources']（按 provider 去重）。
    用于纯外部卡跨源去重时聚合多源，前端订阅时可弹框选择其中一个源。"""
    sources = entry.setdefault("availableSources", [])
    desc = _build_source_descriptor(origin, cal_item)
    if not any(s.get("provider") == desc.get("provider") and s.get("externalId") == desc.get("externalId") for s in sources):
        sources.append(desc)



def _get_subscription_providers(scraper_manager: "ScraperManager") -> list:
    """返回所有「支持订阅/探索」且已启用的弹幕源 provider 名（如 ['bilibili']）。"""
    providers = []
    for name, scraper in scraper_manager.scrapers.items():
        setting = scraper_manager.scraper_settings.get(name, {})
        if not setting.get("isEnabled", True):
            continue
        if getattr(scraper, "supports_subscription", False):
            providers.append(name)
    return providers


async def _sync_scraper_calendars(
    session: AsyncSession, scraper_manager: "ScraperManager", providers: list
) -> int:
    """拉取弹幕源（如 Bilibili）的番剧时间表并落库 external_calendar_item。

    供 weekly 首屏（表数据过期时）与「同步日程」按钮调用。返回写入条目数。
    """
    from src.db.crud import external_calendar as ext_cal_crud

    total = 0
    for provider in providers:
        scraper = scraper_manager.scrapers.get(provider)
        if not scraper:
            continue
        try:
            items = await scraper.fetch_subscription_calendar()
        except NotImplementedError:
            continue
        except Exception as e:
            logger.warning(f"日历同步：源 '{provider}' 拉取失败: {e}")
            continue
        if items:
            total += await ext_cal_crud.upsert_items(session, provider, items)
    return total



class SubscribeRequest(BaseModel):
    """日历订阅请求"""
    animeTitle: str
    mediaType: str = "tv_series"  # tv_series 或 movie
    season: Optional[int] = None
    traktTmdbId: Optional[str] = None
    traktId: Optional[str] = None
    bangumiId: Optional[str] = None
    # 来源 provider（用于定位 external_calendar_item 记录）
    provider: Optional[str] = None
    externalId: Optional[str] = None
    # 是否立即触发一次订阅轮询（导入任务）
    runNow: bool = True
    # 选中的集列表（订阅合集部分集时：立即导入这些集 + 订阅整个合集）
    selectedEpisodes: Optional[list[str]] = None


class BatchSubscribeRequest(BaseModel):
    """批量订阅请求"""
    items: list[SubscribeRequest]
    runNow: bool = True


class UnsubscribeRequest(BaseModel):
    """取消订阅请求（统一处理本地取消追更 + 外部取消订阅）"""
    provider: Optional[str] = None
    externalId: Optional[str] = None
    sourceId: Optional[int] = None
    bangumiId: Optional[str] = None
    traktId: Optional[str] = None
    traktTmdbId: Optional[str] = None


@router.get("/weekly")
async def get_weekly_calendar(
    session: AsyncSession = Depends(get_db_session),
    user: models.User = Depends(security.get_current_user),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
):
    """
    获取每周番表日历数据。
    通过 MetadataSourceManager 动态调用各元数据源的 get_calendar 方法。
    合并：本地追更 + 各元数据源日历（Bangumi/Trakt）+ 弹幕源番剧时间表（Bilibili）。
    """
    # 1. 本地追更数据
    sources = await crud.get_calendar_sources(session)

    # 构建本地已有的 ID 集合
    local_bgm_ids = set()
    local_trakt_ids = set()
    local_tmdb_ids = set()
    for s in sources:
        if s.get("bangumiId"):
            local_bgm_ids.add(str(s["bangumiId"]))
        if s.get("traktId"):
            local_trakt_ids.add(str(s["traktId"]))
        if s.get("tmdbId"):
            local_tmdb_ids.add(str(s["tmdbId"]))

    # 1.5. 已订阅意向集合（external_calendar_item.is_subscribed=TRUE 的记录）
    from src.db.crud import external_calendar as ext_cal_crud
    subscribed_ids = await ext_cal_crud.get_subscribed_external_ids(session)
    subscribed_bgm_ids = subscribed_ids.get("bangumi", set())
    subscribed_trakt_ids = subscribed_ids.get("trakt", set())
    subscribed_tmdb_ids = subscribed_ids.get("tmdb", set())

    weekly = {i: [] for i in range(1, 8)}
    unscheduled = []

    # 本地条目按外部 ID 索引，便于外部条目命中时聚合来源信息（去重）
    local_by_bgm = {}
    local_by_trakt = {}
    local_by_tmdb = {}
    local_by_source = {}
    local_by_title = {}

    # 本地追更条目
    for s in sources:
        schedule_source = None
        if s.get("airWeekday"):
            if s.get("traktId"):
                schedule_source = "trakt"
            if s.get("bangumiId"):
                schedule_source = "bangumi"

        item = {
            "sourceId": s["sourceId"], "animeId": s["animeId"],
            "animeTitle": s["animeTitle"], "animeType": s["animeType"],
            "season": s["season"], "localImagePath": s["localImagePath"],
            "imageUrl": s["imageUrl"], "providerName": s["providerName"],
            "episodeCount": s["episodeCount"], "latestEpisodeIndex": s["latestEpisodeIndex"],
            "airWeekday": s["airWeekday"], "airTime": s["airTime"],
            "bangumiId": s["bangumiId"], "traktId": s["traktId"], "tmdbId": s.get("tmdbId"),
            "scheduleSource": schedule_source,
            "origin": "local", "isLocal": True,
            # 命中该本地条目的外部来源列表（去重合并用）
            "externalSources": [],
        }
        weekday = s.get("airWeekday")
        local_by_source[str(s["sourceId"])] = item
        if weekday and 1 <= weekday <= 7:
            weekly[weekday].append(item)
        else:
            unscheduled.append(item)
        if s.get("bangumiId"):
            local_by_bgm[str(s["bangumiId"])] = item
        if s.get("traktId"):
            local_by_trakt[str(s["traktId"])] = item
        if s.get("tmdbId"):
            local_by_tmdb[str(s["tmdbId"])] = item
        title_key = _calendar_title_key(s.get("animeTitle"), s.get("season"), s.get("animeType"))
        if title_key:
            local_by_title[title_key] = item

    # 2. 动态获取所有元数据源的日历
    external_counts = {}
    # 跨源去重：记录已进入 weekly/unscheduled 的「纯外部条目」标题键。
    # 防止同一番同时来自 metadata 源(Bangumi/Trakt) 与弹幕源(Bilibili) 时被重复加卡。
    external_title_keys = set()
    # 纯外部卡按标题键索引：同名番来自多个外部源时，后来的源不重复加卡，
    # 而是聚合到首张卡的 availableSources，供前端订阅时弹框选择具体源。
    external_by_title = {}
    try:
        all_calendars = await metadata_manager.get_all_calendars(user)
        for source_name, cal_items in all_calendars.items():
            count = 0
            for cal_item in cal_items:
                bgm_id = cal_item.get("bangumiId")
                trakt_id = cal_item.get("traktId")
                tmdb_id = cal_item.get("tmdbId") or cal_item.get("traktTmdbId")
                local_source_id = cal_item.get("localSourceId")
                # 标识「本地是否已订阅」：命中本地 calendar_sources 或命中订阅意向集合
                is_subscribed = bool(
                    (local_source_id and str(local_source_id) in local_by_source)
                    or (bgm_id and (bgm_id in local_bgm_ids or bgm_id in subscribed_bgm_ids))
                    or (trakt_id and (trakt_id in local_trakt_ids or trakt_id in subscribed_trakt_ids))
                    or (tmdb_id and (str(tmdb_id) in local_tmdb_ids or str(tmdb_id) in subscribed_tmdb_ids))
                )
                # 该外部条目自身的订阅状态（用于前端区分「订阅意向中」vs「本地已建库」）
                item_subscription_status = cal_item.get("subscriptionStatus")

                # 去重合并：若该外部番已存在本地条目，则不单独加卡，
                # 而是把来源信息聚合到本地条目的 externalSources，并补全评分/进度。
                local_item = None
                if local_source_id and str(local_source_id) in local_by_source:
                    local_item = local_by_source[str(local_source_id)]
                elif bgm_id and bgm_id in local_by_bgm:
                    local_item = local_by_bgm[bgm_id]
                elif trakt_id and trakt_id in local_by_trakt:
                    local_item = local_by_trakt[trakt_id]
                elif tmdb_id and str(tmdb_id) in local_by_tmdb:
                    local_item = local_by_tmdb[str(tmdb_id)]
                else:
                    title_key = _calendar_title_key(
                        cal_item.get("animeTitle") or cal_item.get("titleZh"),
                        cal_item.get("season"),
                        cal_item.get("animeType") or "tv_series",
                    )
                    if title_key:
                        local_item = local_by_title.get(title_key)
                if local_item is not None:
                    ext_title = cal_item.get("animeTitle") or cal_item.get("titleZh")
                    local_item["externalSources"].append({
                        "origin": source_name,
                        "provider": source_name,
                        "externalId": cal_item.get("externalId") or bgm_id or trakt_id or tmdb_id,
                        "animeTitle": ext_title,
                        "titleZh": cal_item.get("titleZh"),
                        "bangumiId": bgm_id,
                        "traktId": trakt_id,
                        "tmdbId": str(tmdb_id) if tmdb_id else None,
                        "platformWatchStatus": cal_item.get("platformWatchStatus"),
                        "platformWatchedEpisodes": cal_item.get("platformWatchedEpisodes"),
                        "platformRating": cal_item.get("platformRating"),
                        "rating": cal_item.get("rating"),
                    })
                    if ext_title:
                        titles = local_item.setdefault("externalTitles", [])
                        if ext_title not in titles:
                            titles.append(ext_title)
                    if cal_item.get("titleZh"):
                        titles = local_item.setdefault("externalTitles", [])
                        if cal_item.get("titleZh") not in titles:
                            titles.append(cal_item.get("titleZh"))
                    # 评分/已播/总集数：本地缺失时用外部补全
                    if not local_item.get("rating") and cal_item.get("rating"):
                        local_item["rating"] = cal_item.get("rating")
                    if local_item.get("latestEpisodeIndex") is None and cal_item.get("latestEpisodeIndex") is not None:
                        local_item["latestEpisodeIndex"] = cal_item.get("latestEpisodeIndex")
                    if local_item.get("episodeCount") is None and cal_item.get("episodeCount") is not None:
                        local_item["episodeCount"] = cal_item.get("episodeCount")

                    ext_weekday = cal_item.get("airWeekday")
                    if (not local_item.get("airWeekday")) and ext_weekday and 1 <= ext_weekday <= 7:
                        local_item["airWeekday"] = ext_weekday
                        local_item["airTime"] = local_item.get("airTime") or cal_item.get("airTime")
                        local_item["scheduleSource"] = source_name
                        # 本地条目原本没有播出日程时会在 unscheduled；现在用外部日历补齐后移到对应星期
                        unscheduled = [i for i in unscheduled if i is not local_item]
                        if not any(i is local_item for i in weekly[ext_weekday]):
                            weekly[ext_weekday].append(local_item)

                    continue

                # 跨外部源去重：同名番已由其他外部源加过卡 → 不重复加，
                # 仅把当前源聚合到首张卡的 availableSources（前端订阅时可选源）。
                ext_title_key = _calendar_title_key(
                    cal_item.get("animeTitle") or cal_item.get("titleZh"),
                    cal_item.get("season"),
                    cal_item.get("animeType") or "tv_series",
                )
                if ext_title_key and ext_title_key in external_by_title:
                    existing = external_by_title[ext_title_key]
                    _append_available_source(existing, source_name, cal_item)
                    continue

                # 补全默认字段
                entry = {
                    "sourceId": None, "animeId": None,
                    "animeTitle": cal_item.get("animeTitle", ""),
                    "animeType": "tv_series",
                    "season": None, "localImagePath": None,
                    "imageUrl": cal_item.get("imageUrl"),
                    "providerName": None, "episodeCount": None,
                    "latestEpisodeIndex": None,
                    "airWeekday": cal_item.get("airWeekday"),
                    "airTime": None,
                    "bangumiId": bgm_id, "traktId": trakt_id,
                    "tmdbId": str(tmdb_id) if tmdb_id else None,
                    "traktTmdbId": str(tmdb_id) if tmdb_id else cal_item.get("traktTmdbId"),
                    "scheduleSource": source_name,
                    "origin": source_name, "isLocal": False,
                    # 本地订阅标识（与 isLocal 区分：isLocal 表示该条目源自本地表，
                    # isSubscribed 则表示外部条目对应的本地订阅是否存在）
                    "isSubscribed": is_subscribed,
                    # 平台用户私人状态（OAuth 账号下的「我在追/想看/评分」）
                    "platformWatchStatus": cal_item.get("platformWatchStatus"),
                    "platformWatchedEpisodes": cal_item.get("platformWatchedEpisodes"),
                    "platformRating": cal_item.get("platformRating"),
                    **{k: v for k, v in cal_item.items() if k not in (
                        "animeTitle", "airWeekday", "origin", "isLocal",
                        "bangumiId", "traktId", "imageUrl",
                        "platformWatchStatus", "platformWatchedEpisodes", "platformRating",
                    )},
                }
                # 首张卡自身也登记为一个可订阅源（多源时弹框含本源）
                _append_available_source(entry, source_name, cal_item)
                weekday = cal_item.get("airWeekday")
                if weekday and 1 <= weekday <= 7:
                    weekly[weekday].append(entry)
                    count += 1
                    # 登记跨源去重键：后续同名外部源（含 Bilibili 段）命中则聚合而非重复加卡
                    if ext_title_key:
                        external_title_keys.add(ext_title_key)
                        external_by_title[ext_title_key] = entry
            if count > 0:
                external_counts[source_name] = count
    except Exception as e:
        logger.warning(f"获取外部日历失败: {e}")

    # 3. 弹幕源番剧时间表（Bilibili）：从 external_calendar_item 读取，按 airWeekday 进周列/未知列
    try:
        sub_providers = _get_subscription_providers(scraper_manager)
        if sub_providers:
            scraper_items = await ext_cal_crud.list_calendar_items(
                session, providers=sub_providers, max_age_hours=24
            )
            # 表中无新鲜数据（首次或已过期）→ 同步拉取一次再读
            if not scraper_items:
                synced = await _sync_scraper_calendars(session, scraper_manager, sub_providers)
                if synced:
                    scraper_items = await ext_cal_crud.list_calendar_items(
                        session, providers=sub_providers, max_age_hours=24
                    )
            for cal_item in scraper_items:
                provider = cal_item.get("provider")
                # 弱关联本地条目：命中则视为已订阅，不重复加卡
                title_key = _calendar_title_key(
                    cal_item.get("animeTitle") or cal_item.get("titleZh"),
                    cal_item.get("season"),
                    cal_item.get("animeType") or "tv_series",
                )
                if title_key and title_key in local_by_title:
                    # 命中本地条目：聚合 Bilibili 来源到 externalSources（与元数据源一致），
                    # 使本地卡右上角能竖排显示 Bilibili 角标，而非整个跳过导致无标识。
                    local_item = local_by_title[title_key]
                    ext_title = cal_item.get("animeTitle") or cal_item.get("titleZh")
                    # 去重：同一 provider 已聚合过则不重复加
                    if not any(es.get("origin") == provider for es in local_item["externalSources"]):
                        local_item["externalSources"].append({
                            "origin": provider,
                            "provider": provider,
                            "externalId": cal_item.get("externalId"),
                            "animeTitle": ext_title,
                            "titleZh": cal_item.get("titleZh"),
                            "subscriptionType": cal_item.get("subscriptionType"),
                            "rating": cal_item.get("rating"),
                        })
                        if ext_title:
                            titles = local_item.setdefault("externalTitles", [])
                            if ext_title not in titles:
                                titles.append(ext_title)
                    # 评分/已播/总集数：本地缺失时用 Bilibili 数据补全
                    if not local_item.get("rating") and cal_item.get("rating"):
                        local_item["rating"] = cal_item.get("rating")
                    if local_item.get("latestEpisodeIndex") is None and cal_item.get("latestEpisodeIndex") is not None:
                        local_item["latestEpisodeIndex"] = cal_item.get("latestEpisodeIndex")
                    if local_item.get("episodeCount") is None and cal_item.get("episodeCount") is not None:
                        local_item["episodeCount"] = cal_item.get("episodeCount")
                    continue
                # 跨源去重：同名番已由其他外部源(Bangumi/Trakt) 加入周历 →
                # 不重复加 Bilibili 卡，仅把 Bilibili 聚合为首张卡的可订阅源（前端可选源）。
                if title_key and title_key in external_by_title:
                    _append_available_source(external_by_title[title_key], provider, cal_item)
                    continue
                weekday = cal_item.get("airWeekday")
                entry = {
                    "sourceId": None, "animeId": None,
                    "animeTitle": cal_item.get("animeTitle", ""),
                    "animeType": cal_item.get("animeType") or "tv_series",
                    "season": cal_item.get("season"), "localImagePath": None,
                    "imageUrl": cal_item.get("imageUrl"),
                    "providerName": None, "episodeCount": cal_item.get("episodeCount"),
                    "latestEpisodeIndex": cal_item.get("latestEpisodeIndex"),
                    "airWeekday": weekday, "airTime": cal_item.get("airTime"),
                    "bangumiId": None, "traktId": None, "tmdbId": None,
                    "scheduleSource": provider,
                    "origin": provider, "isLocal": False,
                    "isSubscribed": bool(cal_item.get("isSubscribed")),
                    "subscriptionStatus": cal_item.get("subscriptionStatus"),
                    "rating": cal_item.get("rating"),
                    "provider": provider,
                    "externalId": cal_item.get("externalId"),
                    "subscriptionType": cal_item.get("subscriptionType"),
                }
                # 首张卡自身登记为可订阅源（后续同名源聚合到此）
                _append_available_source(entry, provider, cal_item)
                if weekday and 1 <= weekday <= 7:
                    weekly[weekday].append(entry)
                else:
                    unscheduled.append(entry)  # 无播出星期 → 未知列
                if title_key:
                    external_title_keys.add(title_key)
                    external_by_title[title_key] = entry
                external_counts[provider] = external_counts.get(provider, 0) + 1
    except Exception as e:
        logger.warning(f"获取弹幕源日历失败: {e}")

    # 排序：本地优先
    for day in weekly:
        weekly[day].sort(key=lambda x: (0 if x["isLocal"] else 1, x.get("airTime") or "99:99"))

    local_count = len(sources)
    return {
        "weekly": weekly,
        "unscheduled": unscheduled,
        "stats": {
            "total": local_count + sum(external_counts.values()),
            "local": local_count,
            "scheduled": local_count - len(unscheduled),
            "unscheduled": len(unscheduled),
            **external_counts,
        }
    }


@router.get("/tmdb-poster/{tmdb_id}", summary="按需获取 TMDB 海报（懒加载，免认证）")
async def get_tmdb_poster(
    tmdb_id: int,
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
):
    """前端 <img> 直接指向此端点，浏览器天然懒加载/并发控制。
    委托 TMDB 源查海报 URL（查缓存→TMDB），拿到后 302 重定向（海报 URL 公开可直连）。
    无需 JWT：海报 URL 非敏感信息，且 <img> 标签不便携带 Authorization。"""
    from fastapi.responses import RedirectResponse, Response
    tmdb_source = metadata_manager.sources.get("tmdb")
    if tmdb_source is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    try:
        poster_url = await tmdb_source.get_poster_url(tmdb_id)
    except Exception as e:
        logger.debug(f"获取 TMDB 海报失败 (tmdb_id={tmdb_id}): {e}")
        poster_url = None
    if not poster_url:
        # 404 也缓存一小段时间，避免无海报的条目每次重渲染都打一次请求
        return Response(status_code=status.HTTP_404_NOT_FOUND, headers={"Cache-Control": "public, max-age=3600"})
    # 海报 URL 稳定不变 → 让浏览器长期缓存该 302，避免卡片重渲染/滚动时重复请求
    return RedirectResponse(
        url=poster_url,
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/tmdb-title/{tmdb_id}", summary="按需获取 TMDB 中文标题与年份（懒加载，免认证）")
async def get_tmdb_title(
    tmdb_id: int,
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
):
    """前端对 Trakt 日历条目按需请求，用 TMDB 中文标题/年份覆盖英文原标题。
    无需 JWT：标题/年份非敏感信息。"""
    tmdb_source = metadata_manager.sources.get("tmdb")
    if tmdb_source is None:
        return {"title": None, "year": None}
    try:
        return await tmdb_source.get_title_year(tmdb_id) or {"title": None, "year": None}
    except Exception as e:
        logger.debug(f"获取 TMDB 标题失败 (tmdb_id={tmdb_id}): {e}")
        return {"title": None, "year": None}


@router.post("/clear-cache", summary="清除日历同步缓存（Trakt/Bangumi 日历结果）")
async def clear_calendar_cache(
    session: AsyncSession = Depends(get_db_session),
    user: models.User = Depends(security.get_current_user),
):
    """清除已缓存的日历同步结果，使下次加载重新从外部源拉取最新数据。

    清理范围：
    1. 旧版 Trakt/Bangumi 散落缓存键（向后兼容）
    2. 新版聚合层缓存（region=external_calendar）
    3. 持久化表 external_calendar_item 仅删「纯日历缓存」行：
       - 保留 isSubscribed=True 的订阅意向（用户的订阅不会被清掉）
       - 保留带 parentExternalId 的订阅子候选项（避免下次扫描复活已忽略/已下载的项）
    """
    from src.core.cache import get_cache_backend
    cache = get_cache_backend()
    deleted = 0
    try:
        # 1) 旧版 Trakt/Bangumi 散落缓存（兼容历史数据）
        for pattern in ("trakt_calendar_*", "bangumi_calendar_*"):
            keys = await cache.keys(pattern=pattern, region="metadata")
            for k in keys:
                if await cache.delete(k, region="metadata"):
                    deleted += 1
        # 2) 新版聚合层缓存（region=external_calendar）
        try:
            cleared = await cache.clear(region="external_calendar")
            if isinstance(cleared, int):
                deleted += cleared
        except Exception as e:
            logger.debug(f"清除 external_calendar 缓存失败（忽略）: {e}")
        # 3) 持久化表数据 —— 仅删「纯日历缓存」(isSubscribed=False 且无 parentExternalId)
        # parentExternalId 存在 extraData JSON 内，SQL 难以过滤，故 Python 层筛 ID 后批量删
        try:
            import json as _json
            from sqlalchemy import select as sa_select, delete as sa_delete
            from src.db.orm_models import ExternalCalendarItem
            stmt = sa_select(ExternalCalendarItem.id, ExternalCalendarItem.extraData).where(
                ExternalCalendarItem.isSubscribed == False  # noqa: E712
            )
            rows = (await session.execute(stmt)).all()
            ids_to_delete = []
            for row_id, extra_raw in rows:
                # 跳过订阅扫描产生的子候选项（视频候选/分集候选）
                if extra_raw:
                    try:
                        extra = _json.loads(extra_raw)
                        if isinstance(extra, dict) and extra.get("parentExternalId"):
                            continue
                    except (ValueError, TypeError):
                        pass
                ids_to_delete.append(row_id)
            if ids_to_delete:
                # 分批删（避免超大 IN 子句）
                BATCH = 500
                table_deleted = 0
                for i in range(0, len(ids_to_delete), BATCH):
                    chunk = ids_to_delete[i:i + BATCH]
                    res = await session.execute(
                        sa_delete(ExternalCalendarItem).where(ExternalCalendarItem.id.in_(chunk))
                    )
                    table_deleted += res.rowcount or 0
                await session.commit()
                deleted += table_deleted
                if table_deleted:
                    logger.info(
                        f"清除 external_calendar_item 表 {table_deleted} 条「纯日历缓存」"
                        f"（保留订阅意向 + 子候选项）"
                    )
        except Exception as e:
            logger.warning(f"清除 external_calendar_item 表失败: {e}")
    except Exception as e:
        logger.error(f"清除日历缓存失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"清除缓存失败: {e}")
    return {"message": f"已清除 {deleted} 条日历缓存", "deletedCount": deleted}


@router.post("/sync-bangumi-schedule")
async def sync_bangumi_schedule(
    session: AsyncSession = Depends(get_db_session),
    user: models.User = Depends(security.get_current_user),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
):
    """
    手动同步播出日程。
    阶段1: 没有 ID 的源 → 用搜索自动绑定 bangumiId/traktId
    阶段2: 获取日历数据 → 已有 ID 的源匹配 airWeekday
    """
    sources = await crud.get_calendar_sources(session)
    total_updated = 0
    total_bound = 0
    details = []

    # ====== 阶段1: 自动绑定缺失的 ID ======
    # 获取所有已启用的元数据源名称
    enabled_sources = [
        name for name, setting in metadata_manager.source_settings.items()
        if setting.get('isEnabled') and name in metadata_manager.sources
    ]

    for source_name in enabled_sources:
        id_field = None
        if source_name == "bangumi":
            id_field = "bangumiId"
        elif source_name == "trakt":
            id_field = "traktId"
        else:
            continue  # 其他源暂不处理 ID 绑定

        for s in sources:
            if s.get(id_field):
                continue  # 已有 ID，跳过
            anime_title = s.get("animeTitle", "").strip()
            if not anime_title:
                continue
            try:
                logger.info(f"阶段1: 尝试搜索 '{anime_title}' on {source_name} (id_field={id_field})")
                results = await metadata_manager.search(source_name, anime_title, user)
                logger.info(f"阶段1: '{anime_title}' on {source_name} 返回 {len(results)} 个结果")
                if results:
                    matched = results[0]
                    matched_id = str(matched.id)
                    await crud.update_metadata_ids(
                        session,
                        s["animeId"],
                        commit=False,
                        **{id_field: matched_id},
                    )
                    total_bound += 1
                    logger.info(f"自动绑定: '{anime_title}' → {source_name}:{matched_id} ({matched.title})")
                else:
                    logger.info(f"阶段1: '{anime_title}' on {source_name} 无匹配结果")
            except Exception as e:
                logger.warning(f"搜索匹配 '{anime_title}' on {source_name} 失败: {e}")

    if total_bound > 0:
        # why: 阶段1可能绑定多部作品，统一提交避免循环内部分成功。
        await session.commit()
        details.append(f"自动绑定 {total_bound} 部")
        # 重新查询（ID 已更新）
        sources = await crud.get_calendar_sources(session)

    # ====== 阶段2: 用日历数据匹配 airWeekday ======
    try:
        # force_refresh=True：「同步日程」按钮应绕过缓存与表，强制拉最新数据
        all_calendars = await metadata_manager.get_all_calendars(user, force_refresh=True)
    except Exception as e:
        logger.error(f"获取外部日历失败: {e}")
        all_calendars = {}

    for source_name, cal_items in all_calendars.items():
        updated = 0
        id_field = "bangumiId" if source_name == "bangumi" else "traktId"

        schedule_map = {}
        for cal_item in cal_items:
            ext_id = cal_item.get(id_field)
            weekday = cal_item.get("airWeekday")
            if ext_id and weekday:
                schedule_map[str(ext_id)] = weekday

        for s in sources:
            local_id = s.get(id_field)
            if not local_id or str(local_id) not in schedule_map:
                continue
            new_weekday = schedule_map[str(local_id)]
            if new_weekday != s.get("airWeekday"):
                await crud.update_air_schedule(
                    session,
                    s["animeId"],
                    new_weekday,
                    s.get("airTime"),
                    commit=False,
                )
                updated += 1

        if updated > 0:
            total_updated += updated
            details.append(f"{source_name}: 日程更新 {updated} 部")

    if total_updated > 0:
        # why: 阶段2按外部源批量更新日程，统一提交避免只保存前半批。
        await session.commit()

    # ====== 阶段3: 强制刷新弹幕源番剧时间表（Bilibili）======
    try:
        sub_providers = _get_subscription_providers(scraper_manager)
        if sub_providers:
            synced = await _sync_scraper_calendars(session, scraper_manager, sub_providers)
            if synced:
                details.append(f"番剧时间表: 刷新 {synced} 部")
    except Exception as e:
        logger.warning(f"同步弹幕源时间表失败: {e}")

    if not details:
        details.append("无变更")

    return {
        "message": f"同步完成（{'、'.join(details)}）",
        "updatedCount": total_updated,
        "boundCount": total_bound,
        "details": details,
    }



@router.get("/discover")
async def discover_current_season(
    session: AsyncSession = Depends(get_db_session),
    user: models.User = Depends(security.get_current_user),
):
    """
    当季新番发现 — 从 Bangumi /calendar 获取本季所有在播番剧。
    返回按 weekday 分组的当季番剧列表，标注哪些已在本地弹幕库中。
    """
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get("https://api.bgm.tv/calendar")
        resp.raise_for_status()
        bgm_calendar = resp.json()

    # 获取本地已有的 bangumiId 集合
    from sqlalchemy import select
    from src.db.orm_models import AnimeMetadata
    stmt = select(AnimeMetadata.bangumiId).where(AnimeMetadata.bangumiId.isnot(None))
    result = await session.execute(stmt)
    local_bgm_ids = {str(r) for r in result.scalars().all()}

    weekly = {}
    total = 0
    local_count = 0

    for day_group in bgm_calendar:
        weekday = day_group.get("weekday", {}).get("id", 0)
        items = []
        for bgm in day_group.get("items", []):
            bgm_id = str(bgm.get("id", ""))
            is_local = bgm_id in local_bgm_ids
            if is_local:
                local_count += 1

            images = bgm.get("images") or {}
            items.append({
                "bangumiId": bgm_id,
                "title": bgm.get("name_cn") or bgm.get("name", ""),
                "titleJp": bgm.get("name", ""),
                "airDate": bgm.get("air_date"),
                "airWeekday": bgm.get("air_weekday"),
                "imageUrl": images.get("common") or images.get("medium"),
                "rating": bgm.get("rating", {}).get("score"),
                "rank": bgm.get("rank"),
                "isLocal": is_local,
            })
            total += 1

        weekly[weekday] = items

    return {
        "weekly": weekly,
        "stats": {
            "total": total,
            "localCount": local_count,
        }
    }


def _resolve_provider_external_id(body: SubscribeRequest) -> tuple[Optional[str], Optional[str]]:
    """从订阅请求里推断 (provider, externalId)，用于定位 external_calendar_item 记录。

    前端传过来的优先；否则按 bangumiId / traktTmdbId 兜底（注意 traktTmdbId 实际是 tmdbId）。
    """
    if body.provider and body.externalId:
        return body.provider, str(body.externalId)
    if body.bangumiId:
        return "bangumi", str(body.bangumiId)
    if body.traktTmdbId:
        # Trakt 日历卡片用的是 tmdbId 作为锚点，对应 provider="trakt"
        return "trakt", str(body.traktTmdbId)
    return None, None


async def _trigger_auto_import_task(
    body: SubscribeRequest,
    session: AsyncSession,
    task_manager: TaskManager,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataSourceManager,
    config_manager: ConfigManager,
    rate_limiter: RateLimiter,
    ai_matcher_manager: AIMatcherManager,
    title_recognition_manager,
    oauth_user: Optional[models.User] = None,
) -> Optional[str]:
    """触发一次 auto_search_and_import_task。返回 task_id 或 None。"""
    is_movie = body.mediaType == "movie"
    media_type = AutoImportMediaType.MOVIE if is_movie else AutoImportMediaType.TV_SERIES
    season = None if is_movie else (body.season or 1)

    if body.traktTmdbId:
        search_type = AutoImportSearchType.TMDB
        search_term = str(body.traktTmdbId)
    elif body.bangumiId:
        search_type = AutoImportSearchType.BANGUMI
        search_term = str(body.bangumiId)
    else:
        if not body.animeTitle.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少标题或可用的元数据 ID")
        search_type = AutoImportSearchType.KEYWORD
        search_term = body.animeTitle.strip()

    payload = ControlAutoImportRequest(
        searchType=search_type,
        searchTerm=search_term,
        season=season,
        episode=None,
        mediaType=media_type,
        enableIncrementalRefresh=True,
    )

    # 兜底：本地已存在的 anime → 直接开启所有源的增量追更
    try:
        ext_id_filters = []
        if body.bangumiId:
            ext_id_filters.append(orm_models.AnimeMetadata.bangumiId == str(body.bangumiId))
        if body.traktTmdbId:
            ext_id_filters.append(orm_models.AnimeMetadata.tmdbId == str(body.traktTmdbId))
        if ext_id_filters:
            stmt = select(orm_models.AnimeMetadata.animeId).where(or_(*ext_id_filters))
            res = await session.execute(stmt)
            existing_anime_ids = [row[0] for row in res.all() if row[0] is not None]
            if existing_anime_ids:
                upd = (
                    sql_update(orm_models.AnimeSource)
                    .where(orm_models.AnimeSource.animeId.in_(existing_anime_ids))
                    .values(incrementalRefreshEnabled=True)
                )
                await session.execute(upd)
                await session.commit()
                logger.info(
                    f"日历订阅兜底：已为 {len(existing_anime_ids)} 个本地 anime 开启增量追更"
                )
    except Exception as e:
        logger.warning(f"日历订阅兜底开启追更失败（不影响订阅任务）：{e}")

    # 构建 unique_key 防止重复提交
    unique_key_parts = [search_type.value, search_term, media_type.value]
    if season is not None:
        unique_key_parts.append(f"s{season}")
    unique_key = f"calendar-subscribe-{'-'.join(unique_key_parts)}"

    task_title = f"订阅: {body.animeTitle}"
    if season is not None:
        task_title += f" S{season:02d}"

    task_coro = lambda s, cb: tasks.auto_search_and_import_task(
        payload, cb, s, config_manager, scraper_manager, metadata_manager, task_manager,
        ai_matcher_manager=ai_matcher_manager,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager,
        oauth_user=oauth_user,
    )
    task_id, _ = await task_manager.submit_task(
        task_coro, task_title, unique_key=unique_key,
        task_type="auto_import",
        task_parameters=payload.model_dump(),
    )
    return task_id


@router.post("/subscribe", summary="订阅外部番（标记订阅意向，可选立即执行轮询）")
async def subscribe_calendar_item(
    request: Request,
    body: SubscribeRequest,
    session: AsyncSession = Depends(get_db_session),
    user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
    title_recognition_manager = Depends(get_title_recognition_manager),
):
    """
    订阅日历中的外部番：
    - 默认仅写入「订阅意向」标记（external_calendar_item.is_subscribed=TRUE，status=pending）
    - runNow=True 时同时立即触发 auto_search_and_import_task 把作品加入弹幕库并开启追更
    - 后续无论 runNow 与否，定时追更任务都会扫描 pending 订阅并自动建库
    """
    from src.db.crud import external_calendar as ext_cal_crud

    # 标记订阅意向
    provider, external_id = _resolve_provider_external_id(body)
    if not provider or not external_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无法定位外部条目（缺少 provider/externalId 或 bangumiId/traktTmdbId）",
        )
    init_status = "importing" if body.runNow else "pending"
    marked = await ext_cal_crud.mark_subscribed(
        session,
        provider,
        external_id,
        status=init_status,
        item={
            "animeTitle": body.animeTitle,
            "animeType": body.mediaType,
            "season": body.season,
            "bangumiId": body.bangumiId,
            "traktId": body.traktId,
            "traktTmdbId": body.traktTmdbId,
        },
    )
    if not marked:
        logger.warning(f"订阅标记失败：无法创建或更新订阅记录 ({provider}:{external_id})")

    if not body.runNow:
        return {
            "message": f"'{body.animeTitle}' 已加入订阅，等待定时任务自动处理",
            "taskId": None,
            "subscriptionStatus": "pending",
        }

    # runNow=True：按源类型分流
    # 若是支持订阅的 scraper（合集/UP主等强标识）→ 直接扫描导入，否则 → 走标题搜索
    scraper = scraper_manager.get_scraper(provider)
    is_scraper_subscription = (
        scraper is not None and getattr(scraper, "supports_subscription", False)
    )

    is_limited, retry_after = await rate_limiter.get_global_limit_status()
    if is_limited:
        # 已经标记为 importing 但触发被流控，回退为 pending
        await ext_cal_crud.update_subscription_status(session, provider, external_id, "pending")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"系统流控中，暂时无法立即执行轮询，请约 {retry_after:.0f} 秒后再试。订阅已加入待处理队列。",
        )

    try:
        if is_scraper_subscription:
            # 强标识订阅（合集/UP主）：直接扫描拉取视频 → 建库
            from src.jobs.subscription_scan import scan_and_import_target_task

            task_coro = lambda s, cb: scan_and_import_target_task(
                cb, s, scraper_manager, config_manager, provider, external_id,
                title_recognition_manager, body.selectedEpisodes or None
            )
            task_id, _ = await task_manager.submit_task(
                task_coro,
                title=f"立即导入订阅: {body.animeTitle}",
                unique_key=f"scan-import-{provider}-{external_id}",
                task_type="scan_and_import_target",
                task_parameters={
                    "provider": provider, "externalId": external_id,
                    "title": body.animeTitle, "selectedEpisodes": body.selectedEpisodes,
                },
                run_immediately=True,
            )
        else:
            # 元数据源订阅/纯标题：走原标题搜索建库
            task_id = await _trigger_auto_import_task(
                body, session, task_manager, scraper_manager, metadata_manager,
                config_manager, rate_limiter, ai_matcher_manager, title_recognition_manager,
                oauth_user=models.User.model_validate(user),
            )
    except HTTPException:
        await ext_cal_crud.update_subscription_status(session, provider, external_id, "pending")
        raise
    except Exception as e:
        await ext_cal_crud.update_subscription_status(session, provider, external_id, "failed", increment_failure=True)
        logger.error(f"提交订阅任务失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"提交订阅任务失败: {e}")

    return {
        "message": f"'{body.animeTitle}' 的订阅任务已提交",
        "taskId": task_id,
        "subscriptionStatus": "importing",
    }


@router.post("/subscribe/batch", summary="批量订阅外部番（可选立即执行轮询）")
async def subscribe_calendar_items_batch(
    body: BatchSubscribeRequest,
    session: AsyncSession = Depends(get_db_session),
    user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
    title_recognition_manager = Depends(get_title_recognition_manager),
):
    """批量订阅。runNow 与单条订阅含义一致，作用于本批次所有项。"""
    from src.db.crud import external_calendar as ext_cal_crud
    if not body.items:
        return {"successCount": 0, "failureCount": 0, "results": []}

    results = []
    success_count = 0
    failure_count = 0

    for item in body.items:
        # 强制对齐顶层 runNow（前端可只传顶层）
        item.runNow = body.runNow
        provider, external_id = _resolve_provider_external_id(item)
        if not provider or not external_id:
            failure_count += 1
            results.append({"animeTitle": item.animeTitle, "success": False, "error": "缺少 provider/externalId"})
            continue
        init_status = "importing" if body.runNow else "pending"
        await ext_cal_crud.mark_subscribed(
            session,
            provider,
            external_id,
            status=init_status,
            item={
                "animeTitle": item.animeTitle,
                "animeType": item.mediaType,
                "season": item.season,
                "bangumiId": item.bangumiId,
                "traktId": item.traktId,
                "traktTmdbId": item.traktTmdbId,
            },
        )

        if not body.runNow:
            success_count += 1
            results.append({"animeTitle": item.animeTitle, "success": True, "taskId": None, "subscriptionStatus": "pending"})
            continue

        try:
            task_id = await _trigger_auto_import_task(
                item, session, task_manager, scraper_manager, metadata_manager,
                config_manager, rate_limiter, ai_matcher_manager, title_recognition_manager,
                oauth_user=models.User.model_validate(user),
            )
            success_count += 1
            results.append({"animeTitle": item.animeTitle, "success": True, "taskId": task_id, "subscriptionStatus": "importing"})
        except HTTPException as e:
            await ext_cal_crud.update_subscription_status(session, provider, external_id, "pending")
            failure_count += 1
            results.append({"animeTitle": item.animeTitle, "success": False, "error": e.detail})
        except Exception as e:
            await ext_cal_crud.update_subscription_status(session, provider, external_id, "failed", increment_failure=True)
            failure_count += 1
            results.append({"animeTitle": item.animeTitle, "success": False, "error": str(e)})

    return {
        "successCount": success_count,
        "failureCount": failure_count,
        "results": results,
        "message": f"批量订阅完成：成功 {success_count} 项，失败 {failure_count} 项",
    }


@router.post("/unsubscribe", summary="取消订阅（统一处理本地取消追更 + 外部取消订阅）")
async def unsubscribe_calendar_item(
    body: UnsubscribeRequest,
    session: AsyncSession = Depends(get_db_session),
    user: models.User = Depends(security.get_current_user),
):
    did_something = False

    # 1) 本地条目：直接标记为完结（取消追更），不用 toggle
    if body.sourceId:
        source = await session.get(orm_models.AnimeSource, body.sourceId)
        if source and not source.isFinished:
            source.isFinished = True
            await session.commit()
            did_something = True
        elif source and source.isFinished:
            # 已经是完结状态，视为成功
            did_something = True

    # 2) 外部条目：取消订阅记录
    if body.provider and body.externalId:
        from src.db.crud import external_calendar as ext_cal_crud
        ok = await ext_cal_crud.unsubscribe(session, body.provider, body.externalId)
        if ok:
            did_something = True

    # 3) 外部条目只是命中了本地追更：没有 external_calendar_item 订阅记录时，按元数据 ID 兜底取消本地追更
    if not did_something and (body.bangumiId or body.traktId or body.traktTmdbId):
        conditions = []
        if body.bangumiId:
            conditions.append(orm_models.AnimeMetadata.bangumiId == str(body.bangumiId))
        if body.traktId:
            conditions.append(orm_models.AnimeMetadata.traktId == str(body.traktId))
        if body.traktTmdbId:
            conditions.append(orm_models.AnimeMetadata.tmdbId == str(body.traktTmdbId))
        stmt = (
            select(orm_models.AnimeSource)
            .join(orm_models.AnimeMetadata, orm_models.AnimeMetadata.animeId == orm_models.AnimeSource.animeId)
            .where(or_(*conditions))
        )
        sources = (await session.execute(stmt)).scalars().all()
        for source in sources:
            if not source.isFinished:
                source.isFinished = True
                did_something = True
        if sources:
            did_something = True
            await session.commit()

    if not did_something:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该订阅记录")
    return {"message": "已取消订阅"}
