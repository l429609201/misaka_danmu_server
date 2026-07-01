"""
内置日程同步轮询任务

每15分钟自动从 Bangumi / Trakt 同步追更番剧的播出日程信息。
无需用户手动创建定时任务，启动后自动运行。
"""
import logging

from fastapi import FastAPI

from .base import BasePollingTask

logger = logging.getLogger("ScheduleSync")


class ScheduleSyncPollingTask(BasePollingTask):
    """日程同步内置轮询任务 — 自动从 Bangumi / Trakt 同步 airWeekday"""
    name = "schedule_sync"
    enabled_key = ""          # 始终启用
    interval_key = ""         # 使用硬编码间隔
    default_interval = 15     # 每15分钟
    min_interval = 5          # 最小5分钟
    startup_delay = 90        # 启动后90秒开始（让其他服务先就绪）

    @staticmethod
    async def handler(app: FastAPI) -> None:
        await _schedule_sync_handler(app)


async def _schedule_sync_handler(app: FastAPI) -> None:
    """日程同步核心逻辑 — 复用 MetadataSourceManager 的日历+搜索能力"""
    from src.db import crud, orm_models

    session_factory = app.state.db_session_factory
    metadata_manager = app.state.metadata_manager

    # 获取第一个用户（内置任务没有 request context）
    async with session_factory() as session:
        from sqlalchemy import select
        stmt = select(orm_models.User).limit(1)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if not user:
            return

    # 1. 获取各源日历数据
    try:
        all_calendars = await metadata_manager.get_all_calendars(user)
    except Exception as e:
        logger.error(f"日程同步: 获取外部日历失败: {e}")
        all_calendars = {}

    # 注：即使 all_calendars 全空（在线源全挂），下方仍会尝试 bangumi 离线兜底，故不在此提前 return
    if all_calendars is None:
        all_calendars = {}

    total_updated = 0
    total_bound = 0

    async with session_factory() as session:
        sources = await crud.get_calendar_sources(session)
        if not sources:
            return

        # bangumi 在线日历缺失/为空（api.bgm.tv 常 502）→ 注入离线 bangumi-data 播出星期兜底。
        # 提到循环前处理：get_all_calendars 在 502 时根本不含 "bangumi" key，循环内补不到。
        if not all_calendars.get("bangumi"):
            try:
                from src.services import get_bangumi_data_manager
                mgr = get_bangumi_data_manager()
                if mgr is not None:
                    offline = await mgr.get_offline_air_schedule()
                    if offline:
                        all_calendars["bangumi"] = [
                            {"bangumiId": bgm_id, "airWeekday": info["airWeekday"],
                             "airTime": info.get("airTime")}
                            for bgm_id, info in offline.items()
                        ]
                        logger.info(f"日程同步: bangumi 在线无数据，离线兜底提取 {len(offline)} 部")
            except Exception as e:
                logger.warning(f"日程同步: bangumi 离线兜底失败: {e}")

        for source_name, cal_items in all_calendars.items():
            id_field = "bangumiId" if source_name == "bangumi" else "traktId"

            # 构建 ID → weekday 映射
            schedule_map = {}
            for cal_item in cal_items:
                ext_id = cal_item.get(id_field)
                weekday = cal_item.get("airWeekday")
                if ext_id and weekday:
                    schedule_map[str(ext_id)] = weekday

            for s in sources:
                local_id = s.get(id_field)

                if local_id and str(local_id) in schedule_map:
                    # 已有 ID → 直接匹配日程
                    new_weekday = schedule_map[str(local_id)]
                    if new_weekday != s.get("airWeekday"):
                        await crud.update_air_schedule(session, s["animeId"], new_weekday, s.get("airTime"))
                        total_updated += 1
                elif not local_id:
                    # 没有 ID → 用已有的元数据搜索能力自动匹配
                    anime_title = s.get("animeTitle", "").strip()
                    if not anime_title:
                        continue
                    try:
                        results = await metadata_manager.search(source_name, anime_title, user)
                        if results:
                            matched = results[0]
                            matched_id = str(matched.id)
                            update_fields = {id_field: matched_id}
                            if matched_id in schedule_map:
                                update_fields["airWeekday"] = schedule_map[matched_id]
                            await crud.update_metadata_ids(session, s["animeId"], **update_fields)
                            total_bound += 1
                            total_updated += 1
                            logger.info(f"自动匹配: '{anime_title}' → {source_name}:{matched_id} ({matched.title})")
                    except Exception as e:
                        logger.debug(f"搜索匹配 '{anime_title}' on {source_name} 失败: {e}")

    if total_updated > 0 or total_bound > 0:
        logger.info(f"日程同步完成: 更新{total_updated}部, 自动绑定{total_bound}部")
