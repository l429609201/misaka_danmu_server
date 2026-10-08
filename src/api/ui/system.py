"""
System 相关的 API 端点 - 🚀 薄层路由，业务逻辑在 workflows 层

本模块只负责：
- 路由定义和参数验证
- 依赖注入
- HTTP 响应封装
"""
import asyncio
import json
import logging
from typing import List, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

# 流控重置倒计时使用应用时区，与数据库记录的时间保持一致。
from src.core.timezone import get_now
from src.utils.auth import security
# 直接引用解析模块，避免旧路径在接口调用时才触发导入失败。
from src.utils.parsing.filename_parser import parse_filename
from src.utils.runtime.cancellation import finish_before_cancel
from src.api.ui.rate_limit_response import RateLimitStreamingResponse
from src.services.database_service import DatabaseService
from src.services.service_container import get_database_service
from src.api.dependencies import (
    get_scraper_manager, get_config_service, get_rate_limiter
)
from src.services.scraper_manager import ScraperManager
from src.services.config_service import ConfigService
from src.rate_limiter import RateLimiter
from src.schemas.auth import User
from src.schemas.system import (
    DatabaseInfoResponse,
    VersionCheckResponse,
    ReleasesResponse,
    ParseFilenameRequest,
    DockerStatusResponse,
    RestartResponse,
)
from src.schemas.common import PaginatedCommentResponse
from src.schemas.ui_models import ExternalApiLogInfo
from src.schemas.dandan import Comment
from src.workflows.danmaku_management import read_episode_comments
from src.schemas.control.token import UaRule, UaRuleCreate
from src.workflows.system import (
    workflow_check_version,
    workflow_get_release_history,
    workflow_get_docker_status,
    workflow_get_docker_stats_stream,
    workflow_restart_service,
    workflow_update_service_stream,
    workflow_get_logs,
    workflow_list_log_files,
    workflow_read_log_file,
    workflow_stream_logs,
    workflow_get_database_info,
    workflow_clear_all_caches,
)
from src._version import APP_VERSION, DOCS_URL

from src.schemas.ui_models import (
    RateLimitProviderStatus,
    FallbackRateLimitStatus,
    RateLimitStatusResponse
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== 版本信息 ====================

@router.get("/version", response_model=Dict[str, str], summary="获取应用版本号和文档链接")
async def get_app_version():
    """获取当前后端应用的版本号和文档链接 - 🚀 薄层路由"""
    return {"version": APP_VERSION, "docsUrl": DOCS_URL}


@router.get("/version/check", response_model=VersionCheckResponse, summary="检查应用更新")
async def check_app_update(
    config_service: ConfigService = Depends(get_config_service),
    force_refresh: bool = Query(False, description="强制刷新缓存")
):
    """检查是否有新版本可用 - 🚀 薄层路由，委托给 workflow"""
    result = await workflow_check_version(config_service, force_refresh)
    return VersionCheckResponse(**result)


@router.get("/version/releases", response_model=ReleasesResponse, summary="获取历史版本列表")
async def get_release_history(
    config_service: ConfigService = Depends(get_config_service),
    limit: int = Query(10, description="获取的版本数量", ge=1, le=50)
):
    """获取最近的版本信息 - 🚀 薄层路由，委托给 workflow"""
    releases = await workflow_get_release_history(config_service, limit)
    return ReleasesResponse(releases=releases)


# ==================== 数据库信息 ====================

@router.get("/database-info", response_model=DatabaseInfoResponse, summary="获取数据库和缓存连接信息")
async def get_database_info(
    request: Request,
    current_user: User = Depends(security.get_current_user),
):
    """获取数据库类型、连接池状态、Redis 详细指标 - 🚀 薄层路由，委托给 workflow"""
    info = await workflow_get_database_info(request)
    return DatabaseInfoResponse(**info)


# ==================== 日志管理 ====================

@router.get("/logs", response_model=List[str], summary="获取最新的服务器日志")
async def get_server_logs(current_user: User = Depends(security.get_current_user)):
    """获取存储在内存中的最新日志条目 - 🚀 薄层路由，委托给 workflow"""
    return await workflow_get_logs()


@router.get("/logs/files", summary="列出所有日志文件")
async def get_log_files(current_user: User = Depends(security.get_current_user)):
    """列出日志目录中的所有日志文件 - 🚀 薄层路由，委托给 workflow"""
    return await workflow_list_log_files()


@router.get("/logs/files/{filename}", summary="读取指定历史日志文件")
async def get_log_file_content(
    filename: str,
    tail: int = Query(200, ge=1, description="每批返回行数，默认200"),
    keyword: str = Query("", description="关键词过滤（大小写不敏感），空字符串不过滤"),
    offset: int = Query(0, ge=0, description="已加载条数，用于加载更多"),
    current_user: User = Depends(security.get_current_user),
):
    """
    读取指定日志文件，支持后端关键词过滤和分页加载 - 🚀 薄层路由，委托给 workflow
    
    返回 {"lines": [...], "hasMore": bool, "total": int}
    """
    return await workflow_read_log_file(filename, tail, keyword, offset)


@router.get("/logs/stream", summary="SSE实时日志推送")
async def stream_server_logs(
    current_user: User = Depends(security.get_current_user_no_db_hold)
):
    """使用Server-Sent Events实时推送服务器日志 - 🚀 薄层路由，委托给 workflow"""
    event_generator = await workflow_stream_logs()
    return StreamingResponse(
        event_generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ==================== 缓存管理 ====================

@router.post("/cache/clear", status_code=status.HTTP_200_OK, summary="清除所有缓存")
async def clear_all_caches(
    request: Request,
    current_user: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service)
):
    """清除所有缓存数据（缓存后端 + 数据库） - 🚀 薄层路由，委托给 workflow"""
    return await workflow_clear_all_caches(request, db, current_user)


# ==================== Docker 管理 ====================

@router.get("/docker/status", response_model=DockerStatusResponse, summary="获取 Docker 状态")
async def get_docker_status_endpoint(
    _: User = Depends(security.get_current_user)
) -> DockerStatusResponse:
    """异步获取 Docker 连接状态，阻塞检查由工作流在线程中执行。"""
    status_data = await workflow_get_docker_status()
    return DockerStatusResponse(**status_data)


@router.get("/docker/stats", summary="获取容器资源使用统计（SSE 实时推送）")
async def get_docker_stats_endpoint(
    _: User = Depends(security.get_current_user_no_db_hold)
):
    """
    获取当前容器的资源使用统计信息 - 🚀 薄层路由，委托给 workflow

    包括 CPU、内存、网络 I/O 等实时数据
    """
    # 异步生成器直接交给 StreamingResponse 迭代，不能对生成器使用 await。
    event_generator = workflow_get_docker_stats_stream()
    return StreamingResponse(
        event_generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/restart", response_model=RestartResponse, summary="重启服务")
async def restart_service(
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """
    重启服务 - 🚀 薄层路由，委托给 workflow

    - 如果 Docker socket 可用，通过 Docker API 重启容器
    - 否则通过退出进程触发容器重启（依赖 restart policy）
    """
    result = await workflow_restart_service(current_user.username, config_service)
    return RestartResponse(**result)


@router.get("/update/stream", summary="流式更新服务")
async def stream_update(
    source: str = Query("docker", pattern="^(docker|github)$", description="镜像来源：docker 或 github"),
    current_user: User = Depends(security.get_current_user_no_db_hold),
    config_service: ConfigService = Depends(get_config_service)
) -> StreamingResponse:
    """按前端指定的镜像来源获取更新事件流。"""
    # 与前端 source 参数及工作流签名对齐；生成器由响应负责异步迭代。
    event_generator = workflow_update_service_stream(
        source=source,
        current_user_username=current_user.username,
        config_service=config_service,
    )
    return StreamingResponse(
        event_generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ==================== 工具和调试 ====================

@router.post("/tools/parse-filename", summary="文件名识别测试")
async def parse_filename_test(
    request: ParseFilenameRequest,
    current_user: User = Depends(security.get_current_user),
):
    """调用文件名解析模块，返回识别结果 - 🚀 薄层路由"""
    result = parse_filename(request.filename)
    return result


@router.get("/comment/{episodeId}", response_model=PaginatedCommentResponse, summary="获取指定分集的弹幕")
async def get_comments(
    episodeId: int,
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(100, ge=1, description="每页数量"),
    db: DatabaseService = Depends(get_database_service)
) -> PaginatedCommentResponse:
    """读取分集弹幕文件并分页，不应用播放器输出限制。"""
    # 编排层管理事务与文件锁，路由不再嵌套事务或按 ORM 对象访问弹幕。
    comments_data = await read_episode_comments(db, episodeId)
    if comments_data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")

    total = len(comments_data)
    start = (page - 1) * pageSize
    end = start + pageSize
    comments = [Comment.model_validate(item) for item in comments_data[start:end]]
    return PaginatedCommentResponse(total=total, list=comments)


# ==================== UA 规则管理 ====================

@router.get("/ua-rules", response_model=List[UaRule], summary="获取所有UA规则")
async def get_ua_rules(
    current_user: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service)
):
    """获取所有 UA 规则 - 🚀 薄层路由"""
    async with db.transaction():
        rules = await db.ua_rule.get_all()
    return [UaRule.model_validate(rule) for rule in rules]


@router.post("/ua-rules", response_model=UaRule, status_code=201, summary="添加UA规则")
async def add_ua_rule(
    ruleData: UaRuleCreate,
    currentUser: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service)
):
    """添加新的 UA 规则 - 🚀 薄层路由"""
    async with db.transaction():
        new_rule = await db.ua_rule.create(ruleData.uaString)
    logger.info(f"用户 '{currentUser.username}' 添加了新的UA规则: {ruleData.uaString}")
    return UaRule.model_validate(new_rule)


@router.delete("/ua-rules/{ruleId}", status_code=204, summary="删除UA规则")
async def delete_ua_rule(
    ruleId: int,
    currentUser: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service)
):
    """删除指定的 UA 规则 - 🚀 薄层路由"""
    async with db.transaction():
        success = await db.ua_rule.delete(ruleId)
    if not success:
        raise HTTPException(status_code=404, detail="规则未找到")
    logger.info(f"用户 '{currentUser.username}' 删除了UA规则: {ruleId}")


# ==================== 外部 API 日志 ====================

@router.get("/external-logs", response_model=List[ExternalApiLogInfo], summary="获取最新的外部API访问日志")
async def get_external_api_logs(
    current_user: User = Depends(security.get_current_user),
    db: DatabaseService = Depends(get_database_service)
):
    """获取最新的外部 API 访问日志 - 🚀 薄层路由"""
    async with db.transaction():
        logs = await db.external_log.get_recent_logs(limit=100)
    return [ExternalApiLogInfo.model_validate(log) for log in logs]


# ==================== 流控状态 ====================

@router.get("/rate-limit/status", response_model=RateLimitStatusResponse, summary="获取所有流控规则的状态")
async def get_rate_limit_status(
    request: Request,
    stream: bool = Query(False, description="启用SSE流式推送模式"),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    db: DatabaseService = Depends(get_database_service),
):
    """
    获取所有流控规则的状态 - 🚀 薄层路由

    支持两种模式：
    1. 普通JSON响应（stream=false）
    2. SSE流式推送（stream=true，每秒更新）
    """
    async def build_status() -> RateLimitStatusResponse:
        """从数据库读取当前流控状态并转换为 UI 响应。"""
        global_config = {
            "enabled": rate_limiter.enabled,
            "limit": rate_limiter.global_limit,
            "period": rate_limiter.global_period_seconds,
        }
        async with db.transaction():
            states = await db.rate_limit.get_all()
            scraper_settings = await db.scraper.get_all_scraper_settings()

        states_map = {state.providerName: state for state in states}
        global_state = states_map.get("__global__")
        global_count = global_state.requestCount if global_state else 0
        seconds_until_reset = 0
        if global_state and global_state.lastResetTime:
            elapsed = get_now().replace(tzinfo=None) - global_state.lastResetTime
            seconds_until_reset = max(
                0, int(global_config["period"] - elapsed.total_seconds())
            )

        provider_statuses = []
        for setting in scraper_settings:
            provider_name = setting["providerName"]
            if provider_name == "custom":
                continue
            state = states_map.get(provider_name)
            # 与实际限流读取同一实例的配额，避免面板将有限额的源误报为无限。
            quota: int | str = "∞"
            display_name = provider_name
            try:
                scraper_instance = scraper_manager.get_scraper(provider_name)
                provider_quota = getattr(scraper_instance, "rate_limit_quota", None)
                if provider_quota is not None and provider_quota > 0:
                    quota = provider_quota
                scraper_class = scraper_manager.get_scraper_class(provider_name)
                display_name = getattr(scraper_class, "display_name", None) or provider_name
            except Exception as exc:
                # 单个源的实现异常不应阻断整个流控面板；保留数据库中的源名继续展示。
                logger.warning("流控状态源读取失败，使用数据库名称展示: %s，错误: %s", provider_name, exc)
            provider_statuses.append(RateLimitProviderStatus(
                providerName=provider_name,
                displayName=display_name,
                requestCount=state.requestCount if state else 0,
                quota=quota,
            ))

        match_state = states_map.get("__fallback_match__")
        search_state = states_map.get("__fallback_search__")
        match_count = match_state.requestCount if match_state else 0
        search_count = search_state.requestCount if search_state else 0

        return RateLimitStatusResponse(
            enabled=global_config["enabled"],
            # 透传真实校验状态，避免模型默认 False 隐藏前端安全告警。
            verificationFailed=rate_limiter._verification_failed,
            globalRequestCount=global_count,
            globalLimit=global_config["limit"],
            globalPeriod=f"{global_config['period']} 秒",
            secondsUntilReset=seconds_until_reset,
            providers=provider_statuses,
            fallback=FallbackRateLimitStatus(
                totalCount=match_count + search_count,
                totalLimit=rate_limiter.fallback_limit,
                matchCount=match_count,
                searchCount=search_count,
            ),
        )

    # 正常请求保持静默，仅在状态构建失败时记录异常。
    if not stream:
        return await build_status()

    # SSE 流式推送
    async def event_generator():
        yield ": connected\n\n"
        try:
            while True:
                try:
                    # 状态读取持有短事务，断连时等其释放连接后再结束 SSE。
                    status = await finish_before_cancel(build_status())
                    data = json.dumps(status.model_dump(), ensure_ascii=False)
                except Exception as exc:
                    # SSE 已建立后不能再返回 HTTP 500，发送可解析的错误事件并继续重试。
                    logger.exception("流控状态 SSE 构建失败: %s", exc)
                    data = json.dumps({"error": "流控状态暂时不可用"}, ensure_ascii=False)
                yield f"data: {data}\n\n"
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            # 客户端断开属于正常生命周期，不打印诊断日志。
            pass

    # 接通已有 ASGI 边界诊断，捕获生成器之外的发送异常。
    return RateLimitStreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
