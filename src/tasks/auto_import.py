"""自动搜索和导入任务模块"""
import asyncio
import logging
from typing import Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas import User
from src.schemas.import_schemas import ControlAutoImportRequest
from src.rate_limiter import RateLimiter
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.workflows.title_recognition import TitleRecognitionWorkflow
from src.services.ai_service import AIService
from src.services.config_service import ConfigService
from src.services.metadata_service import MetadataService
from src.services.performance_service import TaskProfiler
from src.schemas.performance import FLOW_AUTO_IMPORT
from src.tasks.import_dispatch import submit_import_task
from src.utils import SearchTimer, SEARCH_TYPE_CONTROL_AUTO_IMPORT
# 业务准备直接引用 Workflow，任务层只保留执行与派发适配。
from src.workflows.auto_import_metadata import prepare_auto_import_metadata
from src.workflows.auto_import_library import check_auto_import_library
from src.workflows.auto_import_source import prepare_library_import
from src.workflows.auto_import_search_preparation import prepare_auto_import_search
from src.workflows.auto_import_candidates import search_auto_import_candidates


logger = logging.getLogger(__name__)


async def auto_search_and_import_task(
    payload: "ControlAutoImportRequest",
    progress_callback: Callable,
    session: AsyncSession,
    config_service: ConfigService,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataService,
    task_manager: TaskManager,
    ai_service: AIService,
    rate_limiter: Optional[RateLimiter] = None,
    api_key: Optional[str] = None,
    title_recognition_manager: Optional[TitleRecognitionWorkflow] = None,
    oauth_user: Optional[User] = None,
):
    """
    全自动搜索并导入的核心任务逻辑。
    """
    # 初始化搜索计时器（打日志）+ 性能统计 profiler（写 DB）
    timer = SearchTimer(SEARCH_TYPE_CONTROL_AUTO_IMPORT, payload.searchTerm, logger)
    timer.start()
    profiler = TaskProfiler(FLOW_AUTO_IMPORT)

    # 【性能优化】AI初始化预热：提前检查是否需要AI，如果需要则启动预热（不阻塞）
    ai_matcher_warmup_task = None
    try:
        auto_import_tmdb_enabled_check = await config_service.get("autoImportEnableTmdbSeasonMapping", "false")
        if auto_import_tmdb_enabled_check.lower() == "true" and await ai_service.is_available():
            ai_matcher_warmup_task = asyncio.create_task(ai_service.get_matcher())
            logger.debug("全自动导入 AI匹配器预热已启动（并行）")
    except Exception as e:
        logger.warning(f"全自动导入 AI预热失败: {e}")

    try:
        # 防御性检查：确保 rate_limiter 已被正确传递。
        if rate_limiter is None:
            error_msg = "任务启动失败：内部错误（速率限制器未提供）。请检查任务提交处的代码。"
            logger.error(f"auto_search_and_import_task was called without a rate_limiter. This is a bug. Payload: {payload}")
            raise ValueError(error_msg)

        search_type = payload.searchType
        search_term = payload.searchTerm
        media_type = payload.mediaType
        season = payload.season

        await progress_callback(5, f"开始处理，类型: {search_type}, 搜索词: {search_term}")

        # 身份解析与元数据策略由 Workflow 统一维护，任务只接收结果。
        user = oauth_user or User(id=1, username="admin")
        prepared = await prepare_auto_import_metadata(
            payload, user, metadata_manager, progress_callback, timer, profiler,
        )
        search_term = prepared["search_term"]
        media_type, season = prepared["media_type"], prepared["season"]
        main_title, aliases = prepared["main_title"], prepared["aliases"]
        year = prepared["year"]

        # 库内去重委托短事务编排，任务只处理提前完成的信号。
        await progress_callback(20, "正在检查媒体库...")
        timer.step_start("媒体库检查")
        existing_anime, stop_message = await check_auto_import_library(
            search_type, search_term, main_title, media_type, season, year,
            payload.episode, title_recognition_manager,
        )
        if stop_message:
            profiler.record_step("媒体库检查", profiler.total_duration_ms)
            await profiler.flush(session)
            raise TaskSuccess(stop_message)


        if existing_anime and payload.episode is not None:
            library_import = await prepare_library_import(
                existing_anime, media_type, season, payload.episode,
                bool(payload.enableIncrementalRefresh),
            )
            if library_import:
                parameters = library_import["parameters"]
                # 快照使用持久化字段名，执行参数显式转换为处理器签名。
                handler_parameters = dict(parameters)
                handler_parameters["enable_incremental_refresh"] = handler_parameters.pop("enableIncrementalRefresh")
                task_coro = task_manager.build_task_coro_factory(
                    "generic_import", **handler_parameters,
                    config_service=config_service, metadata_manager=metadata_manager,
                    manager=scraper_manager, task_manager=task_manager,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )
                execution_task_id, _ = await task_manager.submit_task(
                    task_coro, library_import["title"],
                    unique_key=library_import["unique_key"],
                    task_type="generic_import", task_parameters=parameters,
                    queue_type="download",
                )
                profiler.record_step("触发导入任务（库内源）", profiler.total_duration_ms)
                await profiler.flush(session)
                raise TaskSuccess(f"已使用库内源创建导入任务。执行任务ID: {execution_task_id}")

        # 3. 如果库中不存在，则进行全网搜索
        _dur = timer.step_end(details=f"{'找到' if existing_anime else '未找到'}")
        profiler.record_step("媒体库检查", _dur)
        await progress_callback(40, "媒体库未找到，开始全网搜索...")
        # 搜索身份准备不借用任务会话，网络请求不会延长数据库连接占用。
        await progress_callback(30, "正在获取元数据源别名...")
        main_title, search_title, search_season = await prepare_auto_import_search(
            main_title, aliases, season, payload.episode, config_service,
            metadata_manager, ai_service, title_recognition_manager, oauth_user,
        )
        prepared["main_title"] = main_title
        # 搜索使用识别词映射后的季度，入库身份仍保留原始季度。
        prepared["search_season"] = search_season
        selection = await search_auto_import_candidates(
            session, prepared, search_title, payload.episode, user,
            scraper_manager, metadata_manager, config_service, ai_service,
            title_recognition_manager, progress_callback, timer, profiler,
            ai_matcher_warmup_task,
        )

        # 任务只派发 Workflow 生成的持久化参数，不再重复选择或转换候选。
        await progress_callback(70, "正在创建导入任务...")
        task_title = selection["title"]
        task_parameters = selection["parameters"]
        task_parameters["enableIncrementalRefresh"] = bool(payload.enableIncrementalRefresh)

        execution_task_id = await submit_import_task(
            task_manager=task_manager,
            task_parameters=task_parameters,
            task_title=task_title,
            queue_type="download",
        )
        timer.finish()  # 打印计时报告
        # 写入性能统计（触发导入步骤）
        profiler.record_step("触发导入任务", profiler.total_duration_ms)
        await profiler.flush(session)
        final_message = f"已为最佳匹配源创建导入任务。执行任务ID: {execution_task_id}"
        raise TaskSuccess(final_message)
    except TaskSuccess:
        raise
    except Exception as e:
        # why：任何中途 raise ValueError/TaskFailed 也要确保 flush，避免数据丢失
        await profiler.flush(session)
        raise
    finally:
        # 提前去重结束或取消时回收预热协程，避免游离任务和未读取异常。
        if ai_matcher_warmup_task is not None:
            if not ai_matcher_warmup_task.done():
                ai_matcher_warmup_task.cancel()
            await asyncio.gather(ai_matcher_warmup_task, return_exceptions=True)
        if api_key:
            await scraper_manager.release_search_lock(api_key)
            logger.info(f"自动导入任务已为 API key 释放搜索锁。")

