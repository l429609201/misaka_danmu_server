"""日历聚合编排。

why：`api/ui/calendar.py` 的 weekly 端点原先在函数体内完成三路数据的合并去重
     （本地追更 + 元数据源日历 + 弹幕源番剧时间表），达 331 行，属业务编排而非
     表现层逻辑。本模块将其下沉，使 API 层退回薄层。

分层约定：本模块不得导入 services/task_manager.py，也不得提交任务。
"""

import logging
import re
from typing import Any, Dict, List, Optional

from src.schemas.auth import User
from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


def normalize_calendar_title(title: Optional[str]) -> str:
    """用于本地条目与外部日历条目弱关联的标题归一化。"""
    if not title:
        return ""
    text = str(title).lower()
    text = re.sub(r"[\s\-_:：·?,，.。!！?？'\"“”‘’()[\]（）【】]+", "", text)
    text = re.sub(r"第[0-9一二三四五六七八九十]+季", "", text)
    text = re.sub(r"season[0-9]+|s[0-9]+", "", text)
    return text.strip()


def calendar_title_key(
    title: Optional[str],
    season: Optional[int],
    anime_type: Optional[str],
) -> Optional[tuple]:
    """构造标题弱关联键：(归一化标题, 季度, 类型)。

    Returns:
        三元组键；标题归一化后为空则返回 None
    """
    normalized = normalize_calendar_title(title)
    if not normalized:
        return None
    return normalized, season or 1, anime_type or "tv_series"


def build_source_descriptor(origin: str, cal_item: dict) -> dict:
    """构造统一的「可订阅源描述」对象，供前端多源选择弹框使用。

    字段对齐 subscribeCalendarItem 入参，前端可直接据此发起订阅。
    """
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


def append_available_source(entry: dict, origin: str, cal_item: dict) -> None:
    """把一个外部源追加到 entry['availableSources']（按 provider 去重）。

    用于纯外部卡跨源去重时聚合多源，前端订阅时可弹框选择其中一个源。
    """
    sources = entry.setdefault("availableSources", [])
    desc = build_source_descriptor(origin, cal_item)
    if not any(
        s.get("provider") == desc.get("provider") and s.get("externalId") == desc.get("externalId")
        for s in sources
    ):
        sources.append(desc)


def get_subscription_providers(scraper_manager: "ScraperManager") -> List[str]:
    """返回所有「支持订阅/探索」且已启用的弹幕源 provider 名（如 ['bilibili']）。"""
    providers = []
    for name, scraper in scraper_manager.scrapers.items():
        setting = scraper_manager.scraper_settings.get(name, {})
        if not setting.get("isEnabled", True):
            continue
        if getattr(scraper, "supports_subscription", False):
            providers.append(name)
    return providers


async def sync_scraper_calendars(
    scraper_manager: ScraperManager,
    providers: List[str],
) -> int:
    """拉取弹幕源（如 Bilibili）的番剧时间表并落库 external_calendar_item。

    供 weekly 首屏（表数据过期时）与「同步日程」按钮调用。

    Args:
        scraper_manager: 弹幕源管理器
        providers: 支持订阅的 provider 名列表

    Returns:
        写入的条目总数
    """
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
            db = get_database_service()
            async with db.transaction():
                total += await db.external_calendar.upsert_items(provider, items)
    return total


class _CalendarAggregator:
    """周历聚合器：承载三路数据合并过程中的索引与去重状态。

    why：聚合过程需要同时维护 5 个本地索引（按 sourceId/bangumiId/traktId/
         tmdbId/标题键）与 2 个外部索引，用类持有这些状态比在函数间传递
         7 个字典更清晰，也便于分段实现三路合并。
    """

    def __init__(self) -> None:
        #: 周一至周日的条目列表
        self.weekly: Dict[int, List[dict]] = {i: [] for i in range(1, 8)}
        #: 无播出星期的条目
        self.unscheduled: List[dict] = []
        #: 各外部源贡献的卡片数
        self.external_counts: Dict[str, int] = {}

        # 本地条目索引，便于外部条目命中时聚合来源信息（去重）
        self._local_by_bgm: Dict[str, dict] = {}
        self._local_by_trakt: Dict[str, dict] = {}
        self._local_by_tmdb: Dict[str, dict] = {}
        self._local_by_source: Dict[str, dict] = {}
        self._local_by_title: Dict[tuple, dict] = {}

        # 本地已有的外部 ID 集合，用于判定外部条目是否已建库
        self._local_bgm_ids: set = set()
        self._local_trakt_ids: set = set()
        self._local_tmdb_ids: set = set()

        # 跨源去重：记录已进入 weekly/unscheduled 的「纯外部条目」标题键。
        # 防止同一番同时来自 metadata 源(Bangumi/Trakt) 与弹幕源(Bilibili) 时被重复加卡。
        self._external_title_keys: set = set()
        # 纯外部卡按标题键索引：同名番来自多个外部源时，后来的源不重复加卡，
        # 而是聚合到首张卡的 availableSources，供前端订阅时弹框选择具体源。
        self._external_by_title: Dict[tuple, dict] = {}

        #: 本地追更条目总数
        self.local_count = 0

    def load_local_sources(self, sources: List[dict]) -> None:
        """载入本地追更数据，建立索引并投放到周历。

        Args:
            sources: get_calendar_sources 返回的本地追更记录
        """
        self.local_count = len(sources)
        for s in sources:
            if s.get("bangumiId"):
                self._local_bgm_ids.add(str(s["bangumiId"]))
            if s.get("traktId"):
                self._local_trakt_ids.add(str(s["traktId"]))
            if s.get("tmdbId"):
                self._local_tmdb_ids.add(str(s["tmdbId"]))

            # 日程来源标注：bangumi 优先于 trakt（与原实现的覆盖顺序一致）
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
            self._local_by_source[str(s["sourceId"])] = item
            if weekday and 1 <= weekday <= 7:
                self.weekly[weekday].append(item)
            else:
                self.unscheduled.append(item)

            if s.get("bangumiId"):
                self._local_by_bgm[str(s["bangumiId"])] = item
            if s.get("traktId"):
                self._local_by_trakt[str(s["traktId"])] = item
            if s.get("tmdbId"):
                self._local_by_tmdb[str(s["tmdbId"])] = item
            title_key = calendar_title_key(
                s.get("animeTitle"), s.get("season"), s.get("animeType")
            )
            if title_key:
                self._local_by_title[title_key] = item

    def _find_local_match(self, cal_item: dict) -> Optional[dict]:
        """按 sourceId → bangumiId → traktId → tmdbId → 标题键 的优先级匹配本地条目。

        Args:
            cal_item: 外部日历条目

        Returns:
            命中的本地条目；未命中返回 None
        """
        local_source_id = cal_item.get("localSourceId")
        if local_source_id and str(local_source_id) in self._local_by_source:
            return self._local_by_source[str(local_source_id)]

        bgm_id = cal_item.get("bangumiId")
        if bgm_id and bgm_id in self._local_by_bgm:
            return self._local_by_bgm[bgm_id]

        trakt_id = cal_item.get("traktId")
        if trakt_id and trakt_id in self._local_by_trakt:
            return self._local_by_trakt[trakt_id]

        tmdb_id = cal_item.get("tmdbId") or cal_item.get("traktTmdbId")
        if tmdb_id and str(tmdb_id) in self._local_by_tmdb:
            return self._local_by_tmdb[str(tmdb_id)]

        title_key = calendar_title_key(
            cal_item.get("animeTitle") or cal_item.get("titleZh"),
            cal_item.get("season"),
            cal_item.get("animeType") or "tv_series",
        )
        return self._local_by_title.get(title_key) if title_key else None

    def _enrich_local_from_external(
        self,
        local_item: dict,
        cal_item: dict,
        source_name: str,
    ) -> None:
        """把外部条目的来源信息聚合到已命中的本地条目，并补全缺失字段。

        Args:
            local_item: 命中的本地条目（原地修改）
            cal_item: 外部日历条目
            source_name: 外部源名
        """
        bgm_id = cal_item.get("bangumiId")
        trakt_id = cal_item.get("traktId")
        tmdb_id = cal_item.get("tmdbId") or cal_item.get("traktTmdbId")
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

        for candidate in (ext_title, cal_item.get("titleZh")):
            if candidate:
                titles = local_item.setdefault("externalTitles", [])
                if candidate not in titles:
                    titles.append(candidate)

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
            self.unscheduled = [i for i in self.unscheduled if i is not local_item]
            if not any(i is local_item for i in self.weekly[ext_weekday]):
                self.weekly[ext_weekday].append(local_item)

    def _is_subscribed(self, cal_item: dict, subscribed: Dict[str, set]) -> bool:
        """判定外部条目在本地是否已订阅（已建库或存在订阅意向）。

        Args:
            cal_item: 外部日历条目
            subscribed: 各平台的订阅意向 ID 集合

        Returns:
            已订阅则为 True
        """
        bgm_id = cal_item.get("bangumiId")
        trakt_id = cal_item.get("traktId")
        tmdb_id = cal_item.get("tmdbId") or cal_item.get("traktTmdbId")
        local_source_id = cal_item.get("localSourceId")
        return bool(
            (local_source_id and str(local_source_id) in self._local_by_source)
            or (bgm_id and (bgm_id in self._local_bgm_ids or bgm_id in subscribed.get("bangumi", set())))
            or (trakt_id and (trakt_id in self._local_trakt_ids or trakt_id in subscribed.get("trakt", set())))
            or (tmdb_id and (
                str(tmdb_id) in self._local_tmdb_ids or str(tmdb_id) in subscribed.get("tmdb", set())
            ))
        )

    def merge_metadata_calendars(
        self,
        all_calendars: Dict[str, List[dict]],
        subscribed: Dict[str, set],
    ) -> None:
        """合并各元数据源（Bangumi/Trakt）的日历数据。

        Args:
            all_calendars: {源名: 日历条目列表}
            subscribed: 各平台的订阅意向 ID 集合
        """
        for source_name, cal_items in all_calendars.items():
            count = 0
            for cal_item in cal_items:
                # 去重合并：若该外部番已存在本地条目，则不单独加卡，
                # 而是把来源信息聚合到本地条目的 externalSources，并补全评分/进度。
                local_item = self._find_local_match(cal_item)
                if local_item is not None:
                    self._enrich_local_from_external(local_item, cal_item, source_name)
                    continue

                # 跨外部源去重：同名番已由其他外部源加过卡 → 不重复加，
                # 仅把当前源聚合到首张卡的 availableSources（前端订阅时可选源）。
                ext_title_key = calendar_title_key(
                    cal_item.get("animeTitle") or cal_item.get("titleZh"),
                    cal_item.get("season"),
                    cal_item.get("animeType") or "tv_series",
                )
                if ext_title_key and ext_title_key in self._external_by_title:
                    append_available_source(
                        self._external_by_title[ext_title_key], source_name, cal_item
                    )
                    continue

                bgm_id = cal_item.get("bangumiId")
                trakt_id = cal_item.get("traktId")
                tmdb_id = cal_item.get("tmdbId") or cal_item.get("traktTmdbId")
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
                    "isSubscribed": self._is_subscribed(cal_item, subscribed),
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
                append_available_source(entry, source_name, cal_item)

                weekday = cal_item.get("airWeekday")
                if weekday and 1 <= weekday <= 7:
                    self.weekly[weekday].append(entry)
                    count += 1
                    # 登记跨源去重键：后续同名外部源（含 Bilibili 段）命中则聚合而非重复加卡
                    if ext_title_key:
                        self._external_title_keys.add(ext_title_key)
                        self._external_by_title[ext_title_key] = entry
            if count > 0:
                self.external_counts[source_name] = count

    def merge_scraper_calendars(self, scraper_items: List[dict]) -> None:
        """合并弹幕源（如 Bilibili）的番剧时间表条目。

        Args:
            scraper_items: external_calendar_item 表中的弹幕源条目
        """
        for cal_item in scraper_items:
            provider = cal_item.get("provider")
            title_key = calendar_title_key(
                cal_item.get("animeTitle") or cal_item.get("titleZh"),
                cal_item.get("season"),
                cal_item.get("animeType") or "tv_series",
            )

            # 弱关联本地条目：命中则视为已订阅，不重复加卡
            if title_key and title_key in self._local_by_title:
                # 命中本地条目：聚合 Bilibili 来源到 externalSources（与元数据源一致），
                # 使本地卡右上角能竖排显示 Bilibili 角标，而非整个跳过导致无标识。
                local_item = self._local_by_title[title_key]
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
            if title_key and title_key in self._external_by_title:
                append_available_source(self._external_by_title[title_key], provider, cal_item)
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
            append_available_source(entry, provider, cal_item)

            if weekday and 1 <= weekday <= 7:
                self.weekly[weekday].append(entry)
            else:
                self.unscheduled.append(entry)  # 无播出星期 → 未知列
            if title_key:
                self._external_title_keys.add(title_key)
                self._external_by_title[title_key] = entry
            self.external_counts[provider] = self.external_counts.get(provider, 0) + 1

    def build_response(self) -> Dict[str, Any]:
        """排序并输出最终的周历响应结构。

        Returns:
            含 weekly/unscheduled/stats 的字典
        """
        # 排序：本地优先，其次按播出时间
        for day in self.weekly:
            self.weekly[day].sort(
                key=lambda x: (0 if x["isLocal"] else 1, x.get("airTime") or "99:99")
            )

        return {
            "weekly": self.weekly,
            "unscheduled": self.unscheduled,
            "stats": {
                "total": self.local_count + sum(self.external_counts.values()),
                "local": self.local_count,
                "scheduled": self.local_count - len(self.unscheduled),
                "unscheduled": len(self.unscheduled),
                **self.external_counts,
            },
        }


async def get_weekly_calendar_flow(
    user: User,
    metadata_manager: MetadataService,
    scraper_manager: ScraperManager,
) -> Dict[str, Any]:
    """聚合每周番表日历数据。

    三路数据合并：
        1. 本地追更（calendar_sources）
        2. 各元数据源日历（Bangumi/Trakt，经 MetadataService 动态调用）
        3. 弹幕源番剧时间表（Bilibili，读 external_calendar_item，过期则同步）

    外部条目若能关联到本地条目，则聚合进本地卡的 externalSources 而非重复加卡；
    同名番来自多个外部源时，仅首张卡入列，其余聚合到 availableSources。

    Args:
        user: 当前用户，供元数据源按账号拉取私人状态
        metadata_manager: 元数据源管理器
        scraper_manager: 弹幕源管理器

    Returns:
        含 weekly/unscheduled/stats 的日历数据
    """
    db = get_database_service()
    aggregator = _CalendarAggregator()

    # ── 1. 本地追更数据 ──
    async with db.transaction():
        sources = await db.source.get_calendar_sources()
        # 已订阅意向集合（external_calendar_item.isSubscribed=TRUE 的记录）
        subscribed = await db.external_calendar.get_subscribed_external_ids()
    aggregator.load_local_sources(sources)

    # ── 2. 各元数据源日历 ──
    try:
        all_calendars = await metadata_manager.get_all_calendars(user)
        aggregator.merge_metadata_calendars(all_calendars, subscribed)
    except Exception as e:
        logger.warning(f"获取外部日历失败: {e}")

    # ── 3. 弹幕源番剧时间表 ──
    try:
        sub_providers = get_subscription_providers(scraper_manager)
        if sub_providers:
            async with db.transaction():
                scraper_items = await db.external_calendar.list_calendar_items(
                    providers=sub_providers, max_age_hours=24
                )
            # 表中无新鲜数据（首次或已过期）→ 同步拉取一次再读
            if not scraper_items:
                if await sync_scraper_calendars(scraper_manager, sub_providers):
                    async with db.transaction():
                        scraper_items = await db.external_calendar.list_calendar_items(
                            providers=sub_providers, max_age_hours=24
                        )
            aggregator.merge_scraper_calendars(scraper_items)
    except Exception as e:
        logger.warning(f"获取弹幕源日历失败: {e}")

    return aggregator.build_response()
