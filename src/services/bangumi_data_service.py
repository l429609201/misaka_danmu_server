"""bangumi-data 离线索引查询与基础持久化服务。"""
import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List, Optional

from opencc import OpenCC
from thefuzz import fuzz

from src.services.service_container import get_database_service
from src.utils import parse_search_keyword, normalize_title

logger = logging.getLogger(__name__)

# 匹配 ISO 8601 带时区的时间点（含可选毫秒、Z 或 ±HH:MM 偏移）。
# 用于从 broadcast 重复规则（如 R/2022-01-09T01:00:00.000Z/P7D）中提取中间时间段。
_ISO_DT_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})"
)
_SITE_META_CONFIG_KEY = "bangumiDataSiteMeta"

# 繁转简转换器（懒加载单例）：兜底精确匹配需归一化繁简差异
_t2s_converter = None


def _normalize_title(s: Optional[str]) -> str:
    """标题归一化：繁转简 + 去空格 + 全角转半角 + 小写。用于兜底精确相等匹配。"""
    if not s:
        return ""
    global _t2s_converter
    if _t2s_converter is None:
        try:
            _t2s_converter = OpenCC("t2s")
        except Exception:
            _t2s_converter = False  # 标记不可用，后续跳过繁简转换
    text = s
    if _t2s_converter:
        try:
            text = _t2s_converter.convert(text)
        except Exception:
            pass
    # 全角转半角
    text = "".join(
        chr(ord(ch) - 0xFEE0) if "！" <= ch <= "～" else (" " if ch == "\u3000" else ch)
        for ch in text
    )
    return text.replace(" ", "").lower()


class BangumiDataService:
    """bangumi-data 本地索引实现，生命周期由 MetadataService 持有。"""

    def __init__(self, config_service: Any = None) -> None:
        self.config_service = config_service
        self.logger = logger
        self._mutation_lock = asyncio.Lock()

    async def is_offline_enabled(self) -> bool:
        """离线库总开关判定（bangumiDataOfflineEnabled）。

        why: 该开关此前在三处各自展开（搜索别名增强、元数据别名补充、直链补充源），
        配置 key 与默认值散落易漂移。收口为唯一谓词，调用方只关心 True/False。
        config_service 缺失时按启用处理，与原三处行为一致（避免未注入配置就静默禁用离线库）。
        """
        if self.config_service is None:
            return True
        try:
            raw = await self.config_service.get("bangumiDataOfflineEnabled", "true")
        except Exception as e:
            self.logger.debug(f"bangumi-data: 读取离线开关失败，按启用处理: {e}")
            return True
        return str(raw).lower() == "true"

    @asynccontextmanager
    async def mutation(self) -> AsyncIterator[None]:
        """为同步流程与清理提供共享索引变更互斥边界。"""
        async with self._mutation_lock:
            yield

    async def replace_index(self, rows: List[Dict[str, Any]]) -> int:
        """在调用方声明的变更边界中原子替换已解析索引。"""
        if not rows:
            raise ValueError("bangumi-data 无有效索引记录，拒绝清空现有数据")
        db = get_database_service()
        async with db.transaction():
            return await db.bangumi_data.replace_all(rows)

    async def count(self) -> int:
        """当前索引表条目数。"""
        db = get_database_service()
        async with db.transaction():
            return await db.bangumi_data.count_rows()

    async def clear(self) -> Dict[str, Any]:
        """与同步互斥地清空离线索引。"""
        async with self._mutation_lock:
            db = get_database_service()
            async with db.transaction():
                before = await db.bangumi_data.count_rows()
                await db.bangumi_data.clear()
            self.logger.info("bangumi-data: 已清除离线索引，共 %s 条", before)
            return {"success": True, "count": before}

    # ---------------- 查询 ----------------

    @staticmethod
    def _row_to_aliases(row: SimpleNamespace) -> Dict[str, Any]:
        """把索引行转成统一别名结构（与 alias_service 对齐）。"""
        all_titles = [t for t in (row.titlesAll or "").split("\n") if t]
        # 简体/繁体中文别名：除日文原名与英文名外的剩余项里挑中文（粗略按非 ASCII 判断）
        aliases_cn: List[str] = []
        for t in all_titles:
            if t in (row.titleMain, row.titleEn):
                continue
            aliases_cn.append(t)
        # 优先把 titleZh 放在首位
        if row.titleZh and row.titleZh in aliases_cn:
            aliases_cn.remove(row.titleZh)
            aliases_cn.insert(0, row.titleZh)
        return {
            "name_en": row.titleEn,
            "name_jp": row.titleMain,  # bangumi-data 的 title 即日文原名
            "name_romaji": None,       # bangumi-data 不提供罗马音
            "aliases_cn": aliases_cn,
        }

    async def get_aliases_by_bangumi_id(self, bangumi_id: str) -> Optional[Dict[str, Any]]:
        """按 bangumiId 直查别名（最准确）。"""
        if not bangumi_id:
            return None
        db = get_database_service()
        async with db.transaction():
            row = await db.bangumi_data.by_bangumi_id(str(bangumi_id))
        return self._row_to_aliases(row) if row else None

    async def get_aliases_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """按任意语言标题/别名模糊查询，命中首条则返回别名结构。"""
        title = (title or "").strip()
        if not title:
            return None
        db = get_database_service()
        async with db.transaction():
            row = await db.bangumi_data.aliases_by_title(title)
        return self._row_to_aliases(row) if row else None

    # ---------------- 别名/平台映射服务（搜索增强 + 探索复用） ----------------

    @staticmethod
    def _row_all_titles(row: SimpleNamespace) -> List[str]:
        """取一条记录的全部标题/别名（含日文原名 + 各语言译名，去空去重）。"""
        return [t for t in (row.titlesAll or "").split("\n") if t]

    async def search_rows_by_title(self, title: str, limit: int = 20) -> List[SimpleNamespace]:
        """按任意语言标题/别名模糊查询，返回多条完整记录（供别名增强 / 离线探索复用）。"""
        title = (title or "").strip()
        if not title:
            return []
        db = get_database_service()
        async with db.transaction():
            return await db.bangumi_data.search_title(title, limit)

    async def _search_candidates_relaxed(self, title: str, limit: int = 50) -> List[SimpleNamespace]:
        """候选池放宽：精确子串 LIKE 圈不到时（如搜索词有错别字「更新→更衣」）的兜底圈选。

        做法：把标题切前/后两半，任一半子串命中即入候选池。差 1 字时错字只落在某一半，
        另一半完整保留 → 仍能圈到正确记录。仅圈候选，最终是否采用由 fuzz 阈值 + 唯一性把关。
        标题过短（<6 字）切半无意义且易引入噪音，直接放弃。
        """
        title = (title or "").strip()
        if len(title) < 6:
            return []
        half = len(title) // 2
        head, tail = title[:half], title[half:]
        db = get_database_service()
        async with db.transaction():
            return await db.bangumi_data.search_relaxed(head, tail, limit)

    async def find_bangumi_id_by_exact_title(
        self, title: str, year: Optional[int] = None
    ) -> Optional[str]:
        """模糊相似匹配，命中唯一才返回 bangumiId（搜索软429兜底专用）。

        why：兜底取 BGM id 必须「宁缺毋滥」——错配会给用户错番弹幕。判定阈值与在线 bangumi
        兜底对齐（fuzz≥88，约可容 8 字标题差 1 字的常见错别字），并在归一化（去空格 + 繁转简
        + 小写 + 全半角）后比较。多条命中时用 year 去重；仍多条或零条 → 返回 None（放弃）。

        候选池：先用精确子串 LIKE 圈选；圈空时（错字场景）用前/后半段 LIKE 放宽再圈一次。
        放宽只影响候选池，最终采用与否仍由 fuzz≥88 + 唯一性把关，不放松错配防线。
        """
        title = (title or "").strip()
        if not title:
            return None
        norm_target = _normalize_title(title)
        if not norm_target:
            return None

        # 阈值与在线 bangumi 兜底一致（dandanplay._try_bgmtv_fallback 用 88）
        _FUZZ_THRESHOLD = 88

        # LIKE 只圈候选池，最终判定在 Python 层做归一化 fuzz 相似度
        rows = await self.search_rows_by_title(title, limit=50)
        if not rows:
            # 精确子串圈空（多为错别字）→ 前后半段放宽再圈一次
            rows = await self._search_candidates_relaxed(title, limit=50)
        matched = [
            row for row in rows
            if row.bangumiId and any(
                fuzz.ratio(_normalize_title(t), norm_target) >= _FUZZ_THRESHOLD
                for t in self._row_all_titles(row)
            )
        ]
        if not matched:
            return None
        if len(matched) > 1 and year is not None:
            # 用年份消歧（bangumi-data 每季独立 subject）
            matched = [r for r in matched if r.beginYear == year] or matched
        if len(matched) != 1:
            self.logger.info(
                f"bangumi-data 兜底匹配: '{title}' 命中 {len(matched)} 条（非唯一），放弃"
            )
            return None
        return str(matched[0].bangumiId)

    async def find_series_bangumi_ids(self, title: str) -> List[str]:
        """查同系列全部季的 bangumiId（无季标记搜索词专用，返回主季 + 各后续季）。

        why：搜「更衣人偶坠入爱河」（无季标记）应涵盖全部季；BGM 里每季是独立 subject
        （第一季=333158、第二季=398951）。同系列判定：把每行标题用项目统一的
        parse_search_keyword 拆出「纯标题 + 季号」，纯标题归一化后 == 搜索词系列主名即同系列；
        季号用于「主季在前、各季升序」排序。

        复用 src.utils 的 parse_search_keyword / normalize_title，不自造季度解析正则。
        防误纳同名不同番：以「去季后缀的系列主名严格归一化相等」为准。无命中返回 []。
        """
        # 复用带年份版本，仅取 bgmId（保持原有调用方契约不变）
        return [bid for bid, _ in await self.find_series_bangumi_ids_with_year(title)]

    async def find_series_bangumi_ids_with_year(self, title: str) -> List[tuple]:
        """同 find_series_bangumi_ids，但每项附带 beginYear，返回 [(bangumiId, year)]。

        why：dandanplay bgmtv 软429兜底调 search_by_bangumi_id 时，接口常不返回 startDate，
        借离线库的 beginYear 兜底年份，避免前端「年份未知」。year 缺省为 None。
        """
        title = (title or "").strip()
        if not title:
            return []
        # 搜索词的系列主名（去掉季度后缀后归一化），作为同系列判定基准
        series_norm = _normalize_title(normalize_title(title))
        if not series_norm:
            return []

        rows = await self.search_rows_by_title(title, limit=50)
        # 候选池圈空（错别字等）→ 前后半段放宽再圈一次，与 find_bangumi_id_by_exact_title 一致
        if not rows:
            rows = await self._search_candidates_relaxed(title, limit=50)

        # (season_order, bangumiId, year) 收集后排序；季号缺省（无季标记=主季）按 1 处理
        collected: List[tuple] = []
        seen_ids = set()
        for row in rows:
            if not row.bangumiId or row.bangumiId in seen_ids:
                continue
            for t in self._row_all_titles(row):
                parsed = parse_search_keyword(t)
                row_series_norm = _normalize_title(normalize_title(parsed.get("title") or t))
                if row_series_norm and row_series_norm == series_norm:
                    # 同系列：季号缺省视为第 1 季（主季）
                    order = parsed.get("season") or 1
                    collected.append((order, str(row.bangumiId), getattr(row, "beginYear", None)))
                    seen_ids.add(row.bangumiId)
                    break
        if not collected:
            return []
        collected.sort(key=lambda x: x[0])
        return [(bid, year) for _, bid, year in collected]

    async def get_offline_air_schedule(self) -> Dict[str, Dict[str, Any]]:
        """从离线 bangumi_data_index 提取「在播番剧的播出日程」，供日程同步在在线日历不可用时兜底。

        why：api.bgm.tv/calendar 国内常 502，导致日程同步(schedule_sync)拿不到 airWeekday。
        离线库存有 broadcast(放送周期, 如 R/2022-01-09 23:00:00/P7D)+beginDate/endDate，
        可本地推算播出星期/时间。注意：这里只产出「bangumiId→日程」映射供同步匹配本地番剧，
        不作为日历展示数据源（不会往日历页塞入本地没有的番）。

        在播判定：beginDate ≤ 今天 且 (endDate 为空 或 endDate ≥ 今天)，type=tv，且有 bangumiId。
        返回 { bangumiId: {"airWeekday": int(1-7), "airTime": "HH:MM"} }。
        """
        today_date = datetime.now().date()
        db = get_database_service()
        async with db.transaction():
            rows = await db.bangumi_data.airing_candidates()

        schedule: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            begin_dt = self._parse_naive_dt(row.beginDate)
            if begin_dt is None or begin_dt.date() > today_date:
                continue  # 还没开播
            end_dt = self._parse_naive_dt(row.endDate)
            if end_dt is not None and end_dt.date() < today_date:
                continue  # 已完结
            air_dt = self._broadcast_first_air(row.broadcast)
            if air_dt is None:
                continue
            schedule[str(row.bangumiId)] = {
                "airWeekday": air_dt.isoweekday(),  # 1=周一..7=周日
                "airTime": air_dt.strftime("%H:%M"),
            }
        self.logger.info(f"bangumi-data 离线日程: 提取 {len(schedule)} 部在播番剧的播出星期")
        return schedule

    @staticmethod
    def _parse_naive_dt(s: Optional[str]) -> Optional["datetime"]:
        """解析 sync 存入的本地墙钟时间串（YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD）。失败返回 None。"""
        if not s:
            return None
        s = s.strip()
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None

    @classmethod
    def _broadcast_first_air(cls, broadcast: Optional[str]) -> Optional["datetime"]:
        """从 broadcast 规则(R/<本地时间>/P7D)提取首播时间点（含周几+时刻）。失败返回 None。"""
        if not broadcast:
            return None
        m = _ISO_DT_PATTERN.search(broadcast)
        raw = m.group(0) if m else None
        if raw:
            # broadcast 经 sync 已转本地时区为 naive 串；但正则也可能匹配到原始 ISO，统一尝试
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(raw[:19], fmt)
                except ValueError:
                    continue
        # sync 后的本地串不带 Z/时区，_ISO_DT_PATTERN（要求时区）可能匹配不到 → 手动找 "R/.../P"
        parts = broadcast.split("/")
        if len(parts) >= 2:
            mid = parts[1].strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(mid[:19], fmt)
                except ValueError:
                    continue
        return None

    async def get_search_aliases(self, title: str, limit: int = 5) -> List[str]:
        """搜索别名增强：按关键词命中 bangumi-data，返回去重的全语言别名列表。

        用途：解决「官方主名 vs 平台译名」不一致（如陆译『更衣人偶坠入爱河』
        在动画疯叫『戀上換裝娃娃』）。把这些译名加入弹幕源 search 关键词列表即可命中。
        """
        rows = await self.search_rows_by_title(title, limit=limit)
        aliases: List[str] = []
        seen = set()
        for row in rows:
            for t in self._row_all_titles(row):
                key = t.replace(" ", "")
                if key and key not in seen:
                    seen.add(key)
                    aliases.append(t)
        return aliases

    @staticmethod
    def _sites_to_map(sites_json: Optional[str]) -> Dict[str, str]:
        """把库内存的「原始 sites 数组」JSON 解析成 {platform: id} 映射（兼容旧调用）。"""
        if not sites_json:
            return {}
        try:
            arr = json.loads(sites_json)
        except Exception:
            return {}
        if isinstance(arr, dict):
            # 兼容历史数据：旧版本可能存的是 {platform:id} 字典
            return {k: str(v) for k, v in arr.items() if v is not None}
        result: Dict[str, str] = {}
        for s in (arr or []):
            site = s.get("site")
            sid = s.get("id")
            if site and sid is not None:
                result[site] = str(sid)
        return result

    async def _get_sites_json(self, bangumi_id: str) -> Optional[str]:
        """按 bangumiId 取出库内 sites 原始 JSON 串。"""
        db = get_database_service()
        async with db.transaction():
            row = await db.bangumi_data.by_bangumi_id(str(bangumi_id))
        return row.sites if row else None

    async def get_platform_id(self, bangumi_id: str, platform: str) -> Optional[str]:
        """A3：按 bangumiId 查指定平台的 id（如 bilibili / iqiyi / tmdb）。

        注意：bangumi-data 的 tmdb id 形如 'movie/324443' / 'tv/12345'，调用方需自行处理前缀。
        """
        if not bangumi_id or not platform:
            return None
        sites_map = self._sites_to_map(await self._get_sites_json(bangumi_id))
        return sites_map.get(platform)

    async def get_all_platform_ids(self, bangumi_id: str) -> Dict[str, str]:
        """A3：按 bangumiId 返回全部平台映射 {platform: id}。"""
        if not bangumi_id:
            return {}
        return self._sites_to_map(await self._get_sites_json(bangumi_id))

    # ---------------- 反向解析（id -> 官方 URL） ----------------

    async def get_site_meta(self) -> Dict[str, Dict[str, Any]]:
        """读取动态落库的 siteMeta（站点 -> {title, urlTemplate, type, regions}）。"""
        if not self.config_service:
            return {}
        try:
            raw = await self.config_service.get(_SITE_META_CONFIG_KEY, "")
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    @staticmethod
    def _build_url(template: Optional[str], site_id: str) -> Optional[str]:
        """用 siteMeta 的 urlTemplate 把站点 id 拼成 URL（兼容 {{id}} 与 {id} 两种占位）。"""
        if not template or site_id is None:
            return None
        try:
            return template.replace("{{id}}", "{id}").replace("{id}", str(site_id))
        except Exception:
            return None

    async def build_platform_urls(self, bangumi_id: str) -> List[Dict[str, Any]]:
        """反向解析：把某番各平台 id 用 siteMeta.urlTemplate 拼成官方 URL 列表。

        返回 [{site, id, title, type, url}]，url 拼不出时为 None。
        """
        if not bangumi_id:
            return []
        sites_map = self._sites_to_map(await self._get_sites_json(bangumi_id))
        if not sites_map:
            return []
        site_meta = await self.get_site_meta()
        result: List[Dict[str, Any]] = []
        for site, sid in sites_map.items():
            meta = site_meta.get(site) or {}
            result.append({
                "site": site,
                "id": sid,
                "title": meta.get("title"),
                "type": meta.get("type"),
                "url": self._build_url(meta.get("urlTemplate"), sid),
            })
        return result

    # ---------------- 订阅离线探索 ----------------

    async def discover_offline(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """订阅离线探索：按关键词在本地 bangumi-data 探索可订阅候选（秒搜+多语言+带平台映射）。

        返回统一候选结构 [{provider, type, title, cover, description, payload}]，与在线 discover 对齐。
        provider=bangumi + type=bangumi_subject：复用 Bangumi 源的订阅追更逻辑（payload 含 bangumiId）；
        额外在 payload 附带 sites 平台映射 + 全语言别名，供前端展示与订阅时定位弹幕源。
        """
        rows = await self.search_rows_by_title(query, limit=limit)
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not row.bangumiId:
                continue
            titles = self._row_all_titles(row)
            sites_map = self._sites_to_map(row.sites)
            year = f" · {row.beginYear}" if row.beginYear else ""
            platforms = "、".join(sites_map.keys()) if sites_map else "无平台映射"
            display_title = row.titleZh or row.titleMain
            out.append({
                # 复用 bangumi 源订阅契约：创建订阅时直接走 Bangumi 的 bangumi_subject 逻辑
                "provider": "bangumi",
                "type": "bangumi_subject",
                "title": display_title,
                "cover": None,  # bangumi-data 不含封面
                "description": f"bangumiId {row.bangumiId}{year} · 平台: {platforms}",
                "payload": {
                    "bangumiId": str(row.bangumiId),
                    "title": display_title,
                    "year": row.beginYear,
                    # 以下为 bangumi-data 增强信息（展示/定位用，不影响 bangumi 订阅 validate）
                    "aliases": titles,
                    "sites": sites_map,
                },
                # 标记来源，前端可区分「离线命中」
                "_offlineSource": "bangumi-data",
            })
        return out
