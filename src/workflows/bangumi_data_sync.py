"""离线索引下载、本地加载与配置记录的跨域同步流程。"""

import hashlib
import json
import logging
import re
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from src.core.timezone import get_app_timezone
from src.services.bangumi_data_service import BangumiDataService
from src.services.file_storage_service import get_file_storage_service
from src.core.proxy import get_proxy_middleware

logger = logging.getLogger(__name__)
_ISO_DT_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})")
_OUTPUT_DT_FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_DATA_URLS = [
    "https://unpkg.com/bangumi-data@0.3/dist/data.json",
    "https://cdn.jsdelivr.net/npm/bangumi-data@0.3/dist/data.json",
]
_SITE_META_CONFIG_KEY = "bangumiDataSiteMeta"
_LOCAL_LOAD_RECORD_KEY = "bangumiDataLocalLoadRecord"
_LOCAL_DATA_FILENAME = "data.json"


class BangumiDataSyncWorkflow:
    """编排下载、解析、索引替换和同步配置，不由服务反向调用。"""

    def __init__(self, index_service: BangumiDataService) -> None:
        self.index_service = index_service
        self.config_service = index_service.config_service
        self.logger = logger

    async def _get_data_urls(self) -> List[str]:
        """获取下载地址列表：优先读配置 bangumiDataUrl（逗号分隔多地址回退），缺失则用内置默认。"""
        urls: List[str] = []
        if self.config_service:
            try:
                raw = await self.config_service.get("bangumiDataUrl", "")
                if raw:
                    urls = [u.strip() for u in raw.split(",") if u.strip()]
            except Exception:
                urls = []
        return urls or list(DEFAULT_DATA_URLS)

    @staticmethod
    def _fmt_bytes(n: float) -> str:
        """字节数人性化格式：B / KB / MB。"""
        if n < 1024:
            return f"{n:.0f}B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f}KB"
        return f"{n / (1024 * 1024):.2f}MB"

    async def _fetch_raw(self, progress_callback: Optional[Callable] = None) -> tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
        """从 CDN 拉取 data.json，返回 (items 列表, siteMeta 字典)（items 失败返回 None）。

        :param progress_callback: 可选 async(progress:int, description:str)，流式下载时实时回报
                                  已下载字节数与下载速度，供任务管理器展示。
        """
        # 复用项目统一代理中间件（支持 HTTP/SOCKS 与加速代理）
        try:
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
                    # 流式下载：按 chunk 累计字节并实时回报速度（why：data.json 数 MB，
                    # 国内 CDN 慢时用户需要看到下载进度而非长时间无反馈）
                    async with client.stream("GET", target_url) as resp:
                        resp.raise_for_status()
                        total = int(resp.headers.get("Content-Length") or 0)
                        downloaded = 0
                        chunks = []
                        start = _time.monotonic()
                        last_report = start
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            chunks.append(chunk)
                            downloaded += len(chunk)
                            now = _time.monotonic()
                            # 每 0.5s 回报一次，避免过于频繁刷库/刷通知
                            if progress_callback and (now - last_report >= 0.5):
                                elapsed = max(now - start, 1e-6)
                                speed = downloaded / elapsed
                                if total > 0:
                                    pct = 10 + int(downloaded / total * 50)  # 下载阶段占 10%~60%
                                    desc = (f"下载中 {self._fmt_bytes(downloaded)}/{self._fmt_bytes(total)} "
                                            f"({self._fmt_bytes(speed)}/s)")
                                else:
                                    pct = 30
                                    desc = f"下载中 {self._fmt_bytes(downloaded)} ({self._fmt_bytes(speed)}/s)"
                                try:
                                    await progress_callback(pct, desc)
                                except Exception:
                                    pass
                                last_report = now
                        body = b"".join(chunks)
                    elapsed = max(_time.monotonic() - start, 1e-6)
                    avg_speed = downloaded / elapsed
                    if progress_callback:
                        try:
                            await progress_callback(
                                60, f"下载完成 {self._fmt_bytes(downloaded)} "
                                    f"(平均 {self._fmt_bytes(avg_speed)}/s)，正在解析..."
                            )
                        except Exception:
                            pass
                    data = json.loads(body)
                items = data.get("items") if isinstance(data, dict) else data
                # siteMeta（站点->URL模板）随 data.json 顶层一起下发，动态解析（数组形态无此字段）
                site_meta = data.get("siteMeta") if isinstance(data, dict) else {}
                if items:
                    self.logger.info(
                        f"bangumi-data: 从 {url} 拉取到 {len(items)} 条记录（{self._fmt_bytes(downloaded)}，"
                        f"平均 {self._fmt_bytes(avg_speed)}/s），siteMeta {len(site_meta if site_meta is not None else {})} 个站点"
                    )
                    return items, (site_meta if site_meta is not None else {})
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
    def _to_naive_local(iso_str: Optional[str]) -> Optional[str]:
        """把带时区的 ISO 时间串转成 TZ 环境变量时区下的本地墙钟时间，去时区、去毫秒。

        输入: "2022-01-09T16:00:00.000Z" / "2022-04-03T17:00:00+09:00"
        输出: "2022-01-10 00:00:00"（按 Asia/Shanghai 等 TZ 转换后的无时区串）
        why: data.json 原样带时区，需按部署时区落地为统一可比较的本地时间。
        无时区/空值/解析失败 → 原样返回（不丢数据、不报错）。
        """
        if not iso_str:
            return iso_str
        s = iso_str.strip()
        try:
            # fromisoformat 在部分 Python 版本不认结尾的 Z，先归一为 +00:00
            normalized = s[:-1] + "+00:00" if s.endswith("Z") else s
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return iso_str  # 非标准格式，原样保留
        if dt.tzinfo is None:
            # 本就无时区：只统一格式（去毫秒、空格分隔），不做时区平移
            return dt.strftime(_OUTPUT_DT_FMT)
        # 带时区：平移到应用时区后去掉 tzinfo
        local_dt = dt.astimezone(get_app_timezone()).replace(tzinfo=None)
        return local_dt.strftime(_OUTPUT_DT_FMT)

    @classmethod
    def _broadcast_to_naive_local(cls, broadcast: Optional[str]) -> Optional[str]:
        """转换 broadcast 重复规则里的时间段，保留 R/.../P... 外壳。

        输入: "R/1971-10-05T15:00:00.000Z/P7D"
        输出: "R/1971-10-05 23:00:00/P7D"（中间时间按 TZ 平移去时区，规则部分原样）
        """
        if not broadcast:
            return broadcast
        return _ISO_DT_PATTERN.sub(
            lambda m: cls._to_naive_local(m.group(0)) or m.group(0),
            broadcast,
        )

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

    async def sync(self, progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """全量同步：从 CDN 拉取 → 清表 → 批量写入。返回 {success, count}。

        :param progress_callback: 可选 async(progress:int, description:str)，
                                  下载阶段回报字节数/速度，写库阶段回报进度。
        """
        async with self.index_service.mutation():
            items, site_meta = await self._fetch_raw(progress_callback=progress_callback)
            if not items:
                return {"success": False, "count": 0, "message": "数据拉取失败"}
            if progress_callback:
                try:
                    await progress_callback(70, f"正在解析并写入 {len(items)} 条记录...")
                except Exception:
                    pass
            try:
                count = await self._persist_items(items, site_meta)
            except (ValueError, TypeError, AttributeError) as exc:
                self.logger.warning("bangumi-data: 解析远端数据失败: %s", exc)
                return {"success": False, "count": 0, "message": f"解析失败: {exc}"}
        if progress_callback:
            try:
                await progress_callback(100, f"同步完成，写入 {count} 条")
            except Exception:
                pass
        self.logger.info(f"bangumi-data: 同步完成，写入 {count} 条")
        return {"success": True, "count": count}

    async def _persist_items(self, items: List[Dict[str, Any]], site_meta: Dict[str, Any]) -> int:
        """把原始 items 解析后清表写入 bangumi_data_index，返回写入条数。

        why: 抽出此方法供 CDN 同步 sync() 与本地加载 sync_from_local() 共用，统一写库逻辑。
        """
        rows = []
        if not isinstance(items, list):
            raise ValueError("bangumi-data items 必须为数组")
        if not isinstance(site_meta, dict):
            raise ValueError("bangumi-data siteMeta 必须为对象")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("bangumi-data 条目必须为对象")
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
                # 时间字段去时区：按 TZ 环境变量平移为本地墙钟时间再存（YYYY-MM-DD HH:MM:SS）
                "beginDate": self._to_naive_local((item.get("begin") or "")[:40] or None),
                "endDate": self._to_naive_local((item.get("end") or "")[:40] or None),
                "broadcast": self._broadcast_to_naive_local((item.get("broadcast") or "")[:100] or None),
                "comment": item.get("comment") or None,
                "sites": json.dumps(raw_sites, ensure_ascii=False) if raw_sites else None,
            })

        if not rows:
            raise ValueError("bangumi-data 无有效索引记录，拒绝清空现有数据")
        count = await self.index_service.replace_index(rows)
        # 只在索引提交后更新站点元信息，失败保留原值。
        if site_meta and self.config_service:
            try:
                await self.config_service.set(_SITE_META_CONFIG_KEY, json.dumps(site_meta, ensure_ascii=False))
            except Exception as e:
                self.logger.warning(f"bangumi-data: 保存 siteMeta 失败: {e}")
        return count

    # ---------------- 本地离线文件加载 ----------------

    def _get_local_data_path(self) -> Path:
        """返回打包于 src/data.json 的本地离线数据路径。"""
        return Path(__file__).parent.parent / _LOCAL_DATA_FILENAME

    async def _read_local_load_record(self) -> Dict[str, Any]:
        """读取上次本地加载记录（config 表 JSON 字段）。缺失/损坏返回 {}。"""
        if not self.config_service:
            return {}
        try:
            raw = await self.config_service.get(_LOCAL_LOAD_RECORD_KEY, "")
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    async def sync_from_local(self, force: bool = False) -> Dict[str, Any]:
        """从受限定的打包 data.json 加载离线数据，哈希未变时跳过。"""
        async with self.index_service.mutation():
            path = self._get_local_data_path()
            fs = get_file_storage_service()
            expected = Path(__file__).parent.parent / _LOCAL_DATA_FILENAME
            if fs.canonical_path(path) != expected or fs.is_symlink(path):
                return {"success": False, "count": 0, "skipped": True, "message": "本地数据文件路径不合法"}
            body = await fs.read_bytes(path, max_bytes=64 * 1024 * 1024)
            if body is None:
                return {"success": False, "count": 0, "skipped": True, "message": "本地文件不存在或读取失败"}
            file_hash = hashlib.sha256(body).hexdigest()
            if not force:
                record = await self._read_local_load_record()
                if record.get("hash") == file_hash and await self.index_service.count() > 0:
                    return {"success": True, "count": record.get("count", 0), "skipped": True, "message": "哈希命中，无需重载"}
            try:
                data = json.loads(body)
                items = data.get("items") if isinstance(data, dict) else data
                site_meta = data.get("siteMeta") if isinstance(data, dict) else {}
                if not isinstance(items, list) or not items:
                    raise ValueError("无有效数据")
                count = await self._persist_items(items, site_meta if site_meta is not None else {})
            except (ValueError, TypeError, AttributeError) as e:
                self.logger.warning("bangumi-data: 解析本地数据文件失败: %s", e)
                return {"success": False, "count": 0, "skipped": True, "message": f"解析失败: {e}"}
            if self.config_service:
                try:
                    record = {"hash": file_hash, "count": count,
                              "loadedAt": datetime.now().strftime(_OUTPUT_DT_FMT), "path": str(path)}
                    await self.config_service.set(_LOCAL_LOAD_RECORD_KEY, json.dumps(record, ensure_ascii=False))
                except Exception as e:
                    self.logger.warning("bangumi-data: 保存本地加载记录失败: %s", e)
            return {"success": True, "count": count, "skipped": False, "message": "加载完成"}


async def sync_bangumi_data(index_service: BangumiDataService, progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
    """从远端数据集同步共享离线索引。"""
    return await BangumiDataSyncWorkflow(index_service).sync(progress_callback)


async def load_local_bangumi_data(index_service: BangumiDataService, force: bool = False) -> Dict[str, Any]:
    """从打包文件加载共享索引，默认按文件哈希跳过重复加载。"""
    return await BangumiDataSyncWorkflow(index_service).sync_from_local(force)
