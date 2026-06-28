"""
bangumi-data 离线数据层管理器

将 https://github.com/bangumi-data/bangumi-data 的聚合产物（dist/data.json，CC BY 4.0）
作为本地离线索引，落库到 bangumi_data_index 表，提供：
- sync()                     从 CDN 拉取 data.json，解析并 upsert 到表（供定时任务调用）
- get_aliases_by_title()     按任意语言标题/别名查询，返回全语言别名集（A2 别名补全 / 匹配增强）
- get_aliases_by_bangumi_id()按 bangumiId 直查别名（A2）
- get_platform_id()          按 bangumiId 查指定平台 id（A3 平台直链）
- build_platform_urls()      反向解析：用 siteMeta.urlTemplate 把各平台 id 拼成官方 URL（对接弹幕源）

定位：在线 Bangumi 元数据源的「本地副本兜底」，命中本地则省一次在线请求，不耦合其主链路。
sites 字段原样保留 data.json 的 sites 数组（含各站点 begin/broadcast）；siteMeta 随同步动态落库到配置。
"""
import json
import logging
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select, delete

from src.db import orm_models

logger = logging.getLogger(__name__)

# 默认数据源（v0.3.x 最新聚合产物）；jsDelivr 作为备用镜像。
# 实际地址由配置 bangumiDataUrl 决定（支持逗号分隔多地址回退），此处仅作兜底。
DEFAULT_DATA_URLS = [
    "https://unpkg.com/bangumi-data@0.3/dist/data.json",
    "https://cdn.jsdelivr.net/npm/bangumi-data@0.3/dist/data.json",
]

# siteMeta（平台 -> URL 模板）随 data.json 一起下发，sync() 时动态解析并存入此配置键，
# 反向解析器从配置读取（why：避免写死常量过时，自动跟随数据源更新）。
_SITE_META_CONFIG_KEY = "bangumiDataSiteMeta"


class BangumiDataManager:
    """bangumi-data 本地离线索引管理器（轻量，非在线元数据源）。"""

    def __init__(self, session_factory, config_manager=None):
        self._session_factory = session_factory
        self.config_manager = config_manager
        self.logger = logger

    # ---------------- 同步 ----------------

    async def _get_data_urls(self) -> List[str]:
        """获取下载地址列表：优先读配置 bangumiDataUrl（逗号分隔多地址回退），缺失则用内置默认。"""
        urls: List[str] = []
        if self.config_manager:
            try:
                raw = await self.config_manager.get("bangumiDataUrl", "")
                if raw:
                    urls = [u.strip() for u in raw.split(",") if u.strip()]
            except Exception:
                urls = []
        return urls or list(DEFAULT_DATA_URLS)

    async def _fetch_raw(self) -> tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
        """从 CDN 拉取 data.json，返回 (items 列表, siteMeta 字典)（items 失败返回 None）。"""
        # 复用项目统一代理中间件（支持 HTTP/SOCKS 与加速代理）
        try:
            from src.utils.proxy_middleware import get_proxy_middleware
            proxy_mw = get_proxy_middleware()
        except Exception:
            proxy_mw = None

        data_urls = await self._get_data_urls()
        last_err = None
        for url in data_urls:
            try:
                if proxy_mw is not None:
                    target_url = await proxy_mw.transform_url(url)
                    client = await proxy_mw.create_client(timeout=120.0)
                else:
                    target_url = url
                    client = httpx.AsyncClient(timeout=120.0, follow_redirects=True)
                async with client:
                    resp = await client.get(target_url)
                    resp.raise_for_status()
                    data = resp.json()
                items = data.get("items") if isinstance(data, dict) else data
                # siteMeta（站点->URL模板）随 data.json 顶层一起下发，动态解析（数组形态无此字段）
                site_meta = data.get("siteMeta") if isinstance(data, dict) else {}
                if items:
                    self.logger.info(
                        f"bangumi-data: 从 {url} 拉取到 {len(items)} 条记录，siteMeta {len(site_meta or {})} 个站点"
                    )
                    return items, (site_meta or {})
            except Exception as e:
                last_err = e
                self.logger.warning(f"bangumi-data: 拉取 {url} 失败: {type(e).__name__}: {e}")
        self.logger.error(f"bangumi-data: 所有数据源拉取失败: {last_err}")
        return None, {}

    @staticmethod
    def _flatten_titles(item: Dict[str, Any]) -> tuple[str, str, Optional[str], Optional[str]]:
        """提取 (titles_all 换行串, title_main, title_zh, title_en)。"""
        main = (item.get("title") or "").strip()
        tt = item.get("titleTranslate") or {}
        zh_list = (tt.get("zh-Hans") or []) + (tt.get("zh-Hant") or [])
        en_list = tt.get("en") or []
        all_titles: List[str] = []
        if main:
            all_titles.append(main)
        for key in ("zh-Hans", "zh-Hant", "en", "ja"):
            for v in (tt.get(key) or []):
                if v and v not in all_titles:
                    all_titles.append(v)
        title_zh = zh_list[0] if zh_list else None
        title_en = en_list[0] if en_list else None
        return "\n".join(all_titles), main, title_zh, title_en

    @staticmethod
    def _extract_bangumi_id(item: Dict[str, Any]) -> Optional[str]:
        """从 sites 数组中提取 bangumi 站点 id（用于与库内 bangumiId 桥接）。"""
        for s in (item.get("sites") or []):
            if s.get("site") == "bangumi" and s.get("id") is not None:
                return str(s.get("id"))
        return None

    @staticmethod
    def _begin_year(item: Dict[str, Any]) -> Optional[int]:
        begin = item.get("begin") or ""
        if len(begin) >= 4 and begin[:4].isdigit():
            return int(begin[:4])
        return None

    async def sync(self) -> Dict[str, Any]:
        """全量同步：拉取 → 清表 → 批量写入。返回 {success, count}。"""
        items, site_meta = await self._fetch_raw()
        if not items:
            return {"success": False, "count": 0, "message": "数据拉取失败"}

        # siteMeta 动态落库到配置（供反向解析器拼 URL）；拉取不到则保留上次的值不覆盖
        if site_meta and self.config_manager:
            try:
                await self.config_manager.setValue(_SITE_META_CONFIG_KEY, json.dumps(site_meta, ensure_ascii=False))
            except Exception as e:
                self.logger.warning(f"bangumi-data: 保存 siteMeta 失败: {e}")

        rows = []
        for item in items:
            titles_all, main, title_zh, title_en = self._flatten_titles(item)
            if not main:
                continue
            bgm_id = self._extract_bangumi_id(item)
            # sites 原样保留整段数组（含各站点 begin/broadcast），不再重组，供反向解析使用
            raw_sites = item.get("sites") or []
            rows.append({
                "bangumiId": bgm_id,
                "titleMain": main[:500],
                "titlesAll": titles_all,
                "titleZh": (title_zh or "")[:500] or None,
                "titleEn": (title_en or "")[:500] or None,
                "type": item.get("type"),
                "beginYear": self._begin_year(item),
                # 新增：补全源完整字段（why：原先丢弃，无法支撑详情展示与放送信息）
                "lang": (item.get("lang") or "")[:16] or None,
                "officialSite": (item.get("officialSite") or "")[:500] or None,
                "beginDate": (item.get("begin") or "")[:40] or None,
                "endDate": (item.get("end") or "")[:40] or None,
                "broadcast": (item.get("broadcast") or "")[:100] or None,
                "comment": item.get("comment") or None,
                "sites": json.dumps(raw_sites, ensure_ascii=False) if raw_sites else None,
            })

        async with self._session_factory() as session:
            await session.execute(delete(orm_models.BangumiDataIndex))
            session.add_all([orm_models.BangumiDataIndex(**r) for r in rows])
            await session.commit()
        self.logger.info(f"bangumi-data: 同步完成，写入 {len(rows)} 条")
        return {"success": True, "count": len(rows)}

    async def count(self) -> int:
        """当前索引表条目数。"""
        from sqlalchemy import func
        async with self._session_factory() as session:
            res = await session.execute(select(func.count(orm_models.BangumiDataIndex.id)))
            return int(res.scalar() or 0)

    # ---------------- 查询 ----------------

    @staticmethod
    def _row_to_aliases(row: orm_models.BangumiDataIndex) -> Dict[str, Any]:
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
        async with self._session_factory() as session:
            res = await session.execute(
                select(orm_models.BangumiDataIndex).where(
                    orm_models.BangumiDataIndex.bangumiId == str(bangumi_id)
                ).limit(1)
            )
            row = res.scalar_one_or_none()
            return self._row_to_aliases(row) if row else None

    async def get_aliases_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """按任意语言标题/别名模糊查询，命中首条则返回别名结构。"""
        title = (title or "").strip()
        if not title:
            return None
        async with self._session_factory() as session:
            # 先精确（titleMain/titleZh/titleEn），再回退 titlesAll 模糊
            from sqlalchemy import or_, func as sfunc
            exact = await session.execute(
                select(orm_models.BangumiDataIndex).where(
                    or_(
                        orm_models.BangumiDataIndex.titleMain == title,
                        orm_models.BangumiDataIndex.titleZh == title,
                        orm_models.BangumiDataIndex.titleEn == title,
                    )
                ).limit(1)
            )
            row = exact.scalar_one_or_none()
            if not row:
                fuzzy = await session.execute(
                    select(orm_models.BangumiDataIndex).where(
                        orm_models.BangumiDataIndex.titlesAll.like(f"%{title}%")
                    ).order_by(sfunc.length(orm_models.BangumiDataIndex.titleMain)).limit(1)
                )
                row = fuzzy.scalar_one_or_none()
            return self._row_to_aliases(row) if row else None

    # ---------------- 别名/平台映射服务（搜索增强 + 探索复用） ----------------

    @staticmethod
    def _row_all_titles(row: orm_models.BangumiDataIndex) -> List[str]:
        """取一条记录的全部标题/别名（含日文原名 + 各语言译名，去空去重）。"""
        return [t for t in (row.titlesAll or "").split("\n") if t]

    async def _search_rows_by_title(self, title: str, limit: int = 20) -> List[orm_models.BangumiDataIndex]:
        """按任意语言标题/别名模糊查询，返回多条完整记录（供别名增强 / 离线探索复用）。"""
        title = (title or "").strip()
        if not title:
            return []
        async with self._session_factory() as session:
            from sqlalchemy import or_, func as sfunc
            # 先精确命中（主名/中文名/英文名完全相等）排前，再补 titlesAll 模糊
            stmt = (
                select(orm_models.BangumiDataIndex)
                .where(
                    or_(
                        orm_models.BangumiDataIndex.titleMain == title,
                        orm_models.BangumiDataIndex.titleZh == title,
                        orm_models.BangumiDataIndex.titleEn == title,
                        orm_models.BangumiDataIndex.titlesAll.like(f"%{title}%"),
                    )
                )
                .order_by(sfunc.length(orm_models.BangumiDataIndex.titleMain))
                .limit(limit)
            )
            res = await session.execute(stmt)
            return list(res.scalars().all())

    async def get_search_aliases(self, title: str, limit: int = 5) -> List[str]:
        """搜索别名增强：按关键词命中 bangumi-data，返回去重的全语言别名列表。

        用途：解决「官方主名 vs 平台译名」不一致（如陆译『更衣人偶坠入爱河』
        在动画疯叫『戀上換裝娃娃』）。把这些译名加入弹幕源 search 关键词列表即可命中。
        """
        rows = await self._search_rows_by_title(title, limit=limit)
        aliases: List[str] = []
        seen = set()
        for row in rows:
            for t in self._row_all_titles(row):
                key = t.replace(" ", "")
                if key and key not in seen:
                    seen.add(key)
                    aliases.append(t)
        return aliases

    async def resolve_to_danmaku_sources(self, bangumi_id: str) -> List[Dict[str, Any]]:
        """id 直链：把某番各平台 id 转换成可直接抓弹幕的 (provider, mediaId)。

        返回 [{site, provider, mediaId}]，仅含转换成功的平台（失败的平台由调用方降级别名搜索）。
        bilibili/iqiyi 涉及网络请求，此处并发执行。
        """
        import asyncio
        from src.services.bangumi_platform_resolver import resolve_media_id, SITE_TO_PROVIDER

        sites_map = self._sites_to_map(await self._get_sites_json(bangumi_id))
        if not sites_map:
            return []
        # 仅处理有对应弹幕源的站点；同 provider 去重（保留首个成功）
        tasks = []
        sites_order = []
        for site, raw_id in sites_map.items():
            if site in SITE_TO_PROVIDER:
                sites_order.append((site, raw_id))
                tasks.append(resolve_media_id(site, raw_id))
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: List[Dict[str, Any]] = []
        seen_provider = set()
        for (site, _raw), res in zip(sites_order, results):
            if isinstance(res, Exception) or not res:
                continue
            provider, media_id = res
            if provider in seen_provider:
                continue
            seen_provider.add(provider)
            out.append({"site": site, "provider": provider, "mediaId": media_id})
        return out

    async def resolve_sources_by_title(self, title: str) -> List[Dict[str, Any]]:
        """按关键词命中 bangumi-data 首条记录，直链解析出各平台 (provider, mediaId) + 元信息。

        供搜索补充源使用：返回 [{provider, mediaId, title, type, year}]，title 用中文名便于展示，
        type 映射为系统标准类型。仅含转换成功的平台。无命中或无 bangumiId 返回 []。
        """
        rows = await self._search_rows_by_title(title, limit=1)
        if not rows:
            return []
        row = rows[0]
        if not row.bangumiId:
            return []
        # bangumi-data type: tv/movie/ova/web → 系统标准类型
        sys_type = "movie" if row.type == "movie" else "tv_series"
        display_title = row.titleZh or row.titleMain
        sources = await self.resolve_to_danmaku_sources(str(row.bangumiId))
        for s in sources:
            s["title"] = display_title
            s["type"] = sys_type
            s["year"] = row.beginYear
        return sources

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
        async with self._session_factory() as session:
            res = await session.execute(
                select(orm_models.BangumiDataIndex.sites).where(
                    orm_models.BangumiDataIndex.bangumiId == str(bangumi_id)
                ).limit(1)
            )
            return res.scalar_one_or_none()

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
        if not self.config_manager:
            return {}
        try:
            raw = await self.config_manager.get(_SITE_META_CONFIG_KEY, "")
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
        rows = await self._search_rows_by_title(query, limit=limit)
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


# ---------------- 全局单例 ----------------
_bangumi_data_manager: Optional[BangumiDataManager] = None


def init_bangumi_data_manager(session_factory, config_manager=None) -> "BangumiDataManager":
    """初始化并返回全局 BangumiDataManager 单例（应用启动时调用）。"""
    global _bangumi_data_manager
    _bangumi_data_manager = BangumiDataManager(session_factory, config_manager)
    return _bangumi_data_manager


def get_bangumi_data_manager() -> Optional["BangumiDataManager"]:
    """获取全局 BangumiDataManager 单例（未初始化返回 None）。"""
    return _bangumi_data_manager
