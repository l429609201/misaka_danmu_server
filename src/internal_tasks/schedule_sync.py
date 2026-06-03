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
        return

    if not all_calendars:
        return

    total_updated = 0
    total_bound = 0

    async with session_factory() as session:
        sources = await crud.get_calendar_sources(session)
        if not sources:
            return

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
