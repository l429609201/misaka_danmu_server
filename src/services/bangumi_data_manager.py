"""
bangumi-data 离线数据层管理器

将 https://github.com/bangumi-data/bangumi-data 的聚合产物（dist/data.json，CC BY 4.0）
作为本地离线索引，落库到 bangumi_data_index 表，提供：
- sync()                     从 CDN 拉取 data.json，解析并 upsert 到表（供定时任务调用）
- get_aliases_by_title()     按任意语言标题/别名查询，返回全语言别名集（A2 别名补全 / 匹配增强）
- get_aliases_by_bangumi_id()按 bangumiId 直查别名（A2）
- get_platform_id()          按 bangumiId 查指定平台 id（A3 平台直链）

定位：在线 Bangumi 元数据源的「本地副本兜底」，命中本地则省一次在线请求，不耦合其主链路。
"""
import json
import logging
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import orm_models

logger = logging.getLogger(__name__)

# 默认数据源（v0.3.x 最新聚合产物）；jsDelivr 作为备用镜像。
# 实际地址由配置 bangumiDataUrl 决定（支持逗号分隔多地址回退），此处仅作兜底。
DEFAULT_DATA_URLS = [
    "https://unpkg.com/bangumi-data@0.3/dist/data.json",
    "https://cdn.jsdelivr.net/npm/bangumi-data@0.3/dist/data.json",
]


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

    async def _fetch_raw(self) -> Optional[List[Dict[str, Any]]]:
        """从 CDN 拉取 data.json，返回 items 列表（失败返回 None）。"""
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
                if items:
                    self.logger.info(f"bangumi-data: 从 {url} 拉取到 {len(items)} 条记录")
                    return items
            except Exception as e:
                last_err = e
                self.logger.warning(f"bangumi-data: 拉取 {url} 失败: {type(e).__name__}: {e}")
        self.logger.error(f"bangumi-data: 所有数据源拉取失败: {last_err}")
        return None

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
    def _extract_sites(item: Dict[str, Any]) -> tuple[Optional[str], Dict[str, str]]:
        """提取 (bangumi_id, {platform: id}) 平台映射。"""
        bgm_id = None
        sites_map: Dict[str, str] = {}
        for s in (item.get("sites") or []):
            site = s.get("site")
            sid = s.get("id")
            if not site or sid is None:
                continue
            sites_map[site] = str(sid)
            if site == "bangumi":
                bgm_id = str(sid)
        return bgm_id, sites_map

    @staticmethod
    def _begin_year(item: Dict[str, Any]) -> Optional[int]:
        begin = item.get("begin") or ""
        if len(begin) >= 4 and begin[:4].isdigit():
            return int(begin[:4])
        return None

    async def sync(self) -> Dict[str, Any]:
        """全量同步：拉取 → 清表 → 批量写入。返回 {success, count}。"""
        items = await self._fetch_raw()
        if not items:
            return {"success": False, "count": 0, "message": "数据拉取失败"}

        rows = []
        for item in items:
            titles_all, main, title_zh, title_en = self._flatten_titles(item)
            if not main:
                continue
            bgm_id, sites_map = self._extract_sites(item)
            rows.append({
                "bangumiId": bgm_id,
                "titleMain": main[:500],
                "titlesAll": titles_all,
                "titleZh": (title_zh or "")[:500] or None,
                "titleEn": (title_en or "")[:500] or None,
                "type": item.get("type"),
                "beginYear": self._begin_year(item),
                "sites": json.dumps(sites_map, ensure_ascii=False) if sites_map else None,
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
        # 归一化：去空格，便于跨写法匹配
        norm = title.replace(" ", "")
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

    async def get_platform_id(self, bangumi_id: str, platform: str) -> Optional[str]:
        """A3：按 bangumiId 查指定平台的 id（如 bilibili / iqiyi / tmdb）。

        注意：bangumi-data 的 tmdb id 形如 'movie/324443' / 'tv/12345'，调用方需自行处理前缀。
        """
        if not bangumi_id or not platform:
            return None
        async with self._session_factory() as session:
            res = await session.execute(
                select(orm_models.BangumiDataIndex.sites).where(
                    orm_models.BangumiDataIndex.bangumiId == str(bangumi_id)
                ).limit(1)
            )
            sites_json = res.scalar_one_or_none()
        if not sites_json:
            return None
        try:
            sites_map = json.loads(sites_json)
        except Exception:
            return None
        return sites_map.get(platform)

    async def get_all_platform_ids(self, bangumi_id: str) -> Dict[str, str]:
        """A3：按 bangumiId 返回全部平台映射 {platform: id}。"""
        if not bangumi_id:
            return {}
        async with self._session_factory() as session:
            res = await session.execute(
                select(orm_models.BangumiDataIndex.sites).where(
                    orm_models.BangumiDataIndex.bangumiId == str(bangumi_id)
                ).limit(1)
            )
            sites_json = res.scalar_one_or_none()
        if not sites_json:
            return {}
        try:
            return json.loads(sites_json) or {}
        except Exception:
            return {}


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
