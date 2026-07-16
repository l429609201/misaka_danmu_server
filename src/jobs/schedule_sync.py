"""
日程同步定时任务
自动从 Bangumi / Trakt 同步追更番剧的播出日程（airWeekday / airTime）

工作流程:
1. 检查用户是否已授权 Bangumi / Trakt
2. 已授权 → 拉取平台日历数据
3. 与本地已追更的源做 bangumiId / traktId 匹配
4. 更新 anime_metadata 的 airWeekday 字段
"""
import logging
from typing import Callable, Dict, Any, List, Optional
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.db import crud, orm_models
from src.jobs.base import BaseJob
from src.services import TaskSuccess

logger = logging.getLogger(__name__)

TRAKT_API_BASE = "https://api.trakt.tv"


class ScheduleSyncJob(BaseJob):
    """日程同步 — 自动从 Bangumi / Trakt 同步播出日程"""
    job_type = "scheduleSync"
    job_name = "日程同步"
    job_name_en = "Schedule Sync"
    job_name_tw = "日程同步"
    description = "自动从 Bangumi 和 Trakt 同步追更番剧的播出日程信息（星期几更新），用于日历视图和智能追更调度。"
    description_en = "Auto-sync airing schedule from Bangumi and Trakt for calendar view and smart refresh scheduling."
    description_tw = "自動從 Bangumi 和 Trakt 同步追更番劇的播出日程資訊（星期幾更新），用於日曆檢視和智慧追更排程。"

    async def run(self, session: AsyncSession, progress_callback: Callable):
        """主同步逻辑"""
        results = []
        total_updated = 0

        # 获取所有追更中的源（含 bangumiId / traktId）
        sources = await crud.get_calendar_sources(session)
        if not sources:
            raise TaskSuccess("没有追更中的源，无需同步日程。")

        # ====== Bangumi 日程同步 ======
        await progress_callback(10, "正在从 Bangumi 同步日程...")
        bgm_count = await self._sync_bangumi(session, sources)
        total_updated += bgm_count
        results.append(f"Bangumi: 更新 {bgm_count} 部")

        # ====== Trakt 日程同步 ======
        await progress_callback(50, "正在从 Trakt 同步日程...")
        trakt_count = await self._sync_trakt(session, sources)
        total_updated += trakt_count
        results.append(f"Trakt: 更新 {trakt_count} 部")

        if total_updated > 0:
            # why: Bangumi 与 Trakt 共用同一任务会话，统一提交保证整轮日程更新原子落盘。
            await session.commit()

        summary = "、".join(results)
        raise TaskSuccess(f"日程同步完成。{summary}，共更新 {total_updated} 部。")

    async def _sync_bangumi(self, session: AsyncSession, sources: List[Dict[str, Any]]) -> int:
        """从 Bangumi /calendar 同步 airWeekday；在线 502/失败时用离线 bangumi-data 兜底。"""
        import httpx

        # 构建 bangumiId → weekday 映射（优先在线 /calendar）
        bgm_schedule: Dict[str, int] = {}
        # airTime 映射（仅离线兜底能提供，在线 /calendar 无放送时刻）
        bgm_airtime: Dict[str, str] = {}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get("https://api.bgm.tv/calendar")
                resp.raise_for_status()
                bgm_calendar = resp.json()
            for day_group in bgm_calendar:
                weekday = day_group.get("weekday", {}).get("id")
                if not weekday:
                    continue
                for item in day_group.get("items", []):
                    bgm_id = str(item.get("id"))
                    bgm_schedule[bgm_id] = weekday
        except Exception as e:
            self.logger.warning(f"Bangumi 在线日历获取失败: {e}，改用离线 bangumi-data 提取日程")

        # 在线拿不到（502 等）→ 离线兜底：从 bangumi_data_index 的 broadcast 推算播出星期
        if not bgm_schedule:
            try:
                from src.services import get_bangumi_data_manager
                mgr = get_bangumi_data_manager()
                if mgr is not None:
                    offline = await mgr.get_offline_air_schedule()
                    for bgm_id, info in offline.items():
                        bgm_schedule[bgm_id] = info["airWeekday"]
                        if info.get("airTime"):
                            bgm_airtime[bgm_id] = info["airTime"]
                    if offline:
                        self.logger.info(f"Bangumi 日程: 离线兜底提取 {len(offline)} 部在播番剧")
            except Exception as e:
                self.logger.warning(f"Bangumi 离线日程兜底失败: {e}")

        if not bgm_schedule:
            return 0

        updated = 0
        for s in sources:
            bgm_id = s.get("bangumiId")
            if not bgm_id or bgm_id not in bgm_schedule:
                continue
            new_weekday = bgm_schedule[bgm_id]
            # airTime：离线兜底有则用离线值，否则沿用原值
            new_airtime = bgm_airtime.get(bgm_id, s.get("airTime"))
            if new_weekday != s.get("airWeekday") or new_airtime != s.get("airTime"):
                await crud.update_air_schedule(
                    session,
                    s["animeId"],
                    new_weekday,
                    new_airtime,
                    commit=False,
                )
                updated += 1
                self.logger.info(f"Bangumi 日程更新: '{s['animeTitle']}' → 星期{new_weekday}")

        return updated

    async def _sync_trakt(self, session: AsyncSession, sources: List[Dict[str, Any]]) -> int:
        """从 Trakt 日历 API 同步 airWeekday"""
        import httpx
        import json

        # 获取第一个有 Trakt OAuth 的用户凭据
        stmt = select(orm_models.OauthCredential).where(
            orm_models.OauthCredential.provider == "trakt",
            orm_models.OauthCredential.accessToken.isnot(None),
        ).limit(1)
        cred = (await session.execute(stmt)).scalar_one_or_none()
        if not cred:
            self.logger.info("Trakt 日程同步: 无用户授权，跳过")
            return 0

        # 从 extraData 获取 client_id
        access_token = cred.accessToken
        client_id = ""
        if cred.extraData:
            try:
                extra = json.loads(cred.extraData)
                client_id = extra.get("clientId", "")
            except (json.JSONDecodeError, TypeError):
                pass

        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "Authorization": f"Bearer {access_token}",
        }
        if client_id:
            headers["trakt-api-key"] = client_id
        else:
            self.logger.warning("Trakt 日程同步: 缺少 client_id (trakt-api-key)，跳过")
            return 0

        # 获取未来7天的日历
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{TRAKT_API_BASE}/calendars/my/shows/{today}/7",
                    headers=headers,
                )
                resp.raise_for_status()
                trakt_calendar = resp.json()
        except Exception as e:
            self.logger.error(f"Trakt 日历获取失败: {e}")
            return 0

        # 构建 traktId → weekday 映射
        trakt_schedule: Dict[str, int] = {}
        for entry in trakt_calendar:
            first_aired = entry.get("first_aired", "")
            show = entry.get("show", {})
            trakt_id = str(show.get("ids", {}).get("trakt", ""))
            if first_aired and trakt_id:
                try:
                    # first_aired 是 ISO 格式，提取星期几 (1=周一 ... 7=周日)
                    air_dt = datetime.fromisoformat(first_aired.replace("Z", "+00:00"))
                    weekday = air_dt.isoweekday()
                    trakt_schedule[trakt_id] = weekday
                except (ValueError, TypeError):
                    pass

        updated = 0
        for s in sources:
            trakt_id = s.get("traktId")
            if not trakt_id or trakt_id not in trakt_schedule:
                continue
            new_weekday = trakt_schedule[trakt_id]
            if new_weekday != s.get("airWeekday"):
                await crud.update_air_schedule(
                    session,
                    s["animeId"],
                    new_weekday,
                    s.get("airTime"),
                    commit=False,
                )
                updated += 1
                self.logger.info(f"Trakt 日程更新: '{s['animeTitle']}' → 星期{new_weekday}")

        return updated
