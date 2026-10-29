"""通用导入首集验证及身份准备编排。"""
from typing import Any, Callable, Dict, List, Tuple

from src.services.service_container import get_database_service
from src.utils.diagnostics.task_exceptions import TaskFailed
from src.workflows.import_candidates import ImportCandidateRejected
from src.workflows.import_identity_preparation import prepare_generic_identity


async def prepare_generic_import(
    parameters: Dict[str, Any], episodes: List[Any], scraper: Any,
    rate_limiter: Any, metadata_manager: Any, title_recognition_manager: Any,
    progress_callback: Callable, profiler: Any,
) -> Tuple[int, int, bool, List[Any]]:
    """只有首集弹幕验证通过才提交身份，其他异常原样交给任务入口。"""
    first_episode = episodes[0]
    await progress_callback(20, f"正在验证数据源有效性: {first_episode.title}")
    is_fallback = parameters.get("is_fallback", False)
    fallback_type = parameters.get("fallback_type")
    async with profiler.step("验证第一集弹幕"):
        if is_fallback:
            if not fallback_type:
                raise ValueError("后备任务必须指定fallback_type参数")
            await rate_limiter.check_fallback(fallback_type, scraper.provider_name)
        else:
            await rate_limiter.check(scraper.provider_name)
        first_comments = await scraper.get_comments(
            first_episode.episodeId,
            progress_callback=lambda p, msg: progress_callback(20 + p * 0.1, msg),
            pool=fallback_type if is_fallback else "global",
        )
    # 仅空结果允许候选顺延，建库及网络异常不得转换成候选拒绝。
    if not first_comments:
        raise ImportCandidateRejected("数据源验证失败，未能获取到任何弹幕，未创建数据库条目。")
    source_id = parameters.get("only_missing_source_id")
    if source_id is not None:
        # 补缺只写入原有来源，不重新建库、拉海报或调整来源归属。
        db = get_database_service()
        async with db.transaction():
            source = await db.source.get_anime_source_info(source_id)
        if not source:
            raise TaskFailed(f"补全缺集失败，找不到源 ID {source_id}。")
        return source["animeId"], source_id, False, first_comments
    await progress_callback(30, "数据源验证成功，正在创建数据库条目...")
    anime_id, source_id, image_failed = await prepare_generic_identity(
        parameters, metadata_manager, title_recognition_manager,
    )
    return anime_id, source_id, image_failed, first_comments
