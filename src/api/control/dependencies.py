"""
外部控制API的依赖注入函数
"""

import re
import logging
import secrets
import ipaddress
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyQuery
from src.services.service_container import get_database_service
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.services.scheduler import SchedulerManager
from src.services.metadata_service import MetadataService
from src.services.config_service import ConfigService
from src.rate_limiter import RateLimiter
from src.services.ai_service import AIService
from src.api.middleware import normalize_ip
from src.utils.diagnostics.audit_logging import build_audit_request_headers, capture_audit_request_body

logger = logging.getLogger(__name__)


# --- Helper Functions ---

def _normalize_for_filtering(title: str) -> str:
    """Removes brackets and standardizes a title for fuzzy matching."""
    if not title:
        return ""
    # Remove content in brackets
    title = re.sub(r'[\[【(（].*?[\]】)）]', '', title)
    # Normalize to lowercase, remove spaces, and standardize colons
    return title.lower().replace(" ", "").replace("：", ":").strip()



# --- 依赖项函数 ---

def get_scraper_manager(request: Request) -> ScraperManager:
    """依赖项：从应用状态获取 Scraper 管理器"""
    return request.app.state.scraper_manager


def get_metadata_service(request: Request) -> MetadataService:
    """从应用状态获取统一元数据服务。"""
    return request.app.state.metadata_service


def get_task_manager(request: Request) -> TaskManager:
    """依赖项：从应用状态获取任务管理器"""
    return request.app.state.task_manager


def get_scheduler_manager(request: Request) -> SchedulerManager:
    """依赖项：从应用状态获取定时任务调度器"""
    return request.app.state.scheduler_manager


def get_config_service(request: Request) -> ConfigService:
    """依赖项：获取启动流程挂载的配置服务，沿用实际的状态属性名。"""
    return request.app.state.config_service


def get_rate_limiter(request: Request) -> RateLimiter:
    """依赖项：从应用状态获取速率限制器"""
    return request.app.state.rate_limiter


def get_ai_service(request: Request) -> AIService:
    """依赖项：从应用状态获取共享 AI 服务。"""
    return request.app.state.ai_service


def get_title_recognition_manager(request: Request):
    """依赖项：从应用状态获取标题识别管理器"""
    return request.app.state.title_recognition_manager


# API Key安全方案
api_key_scheme = APIKeyQuery(
    name="api_key",
    auto_error=False,
    description="用于所有外部控制API的访问密钥。"
)


async def verify_api_key(
    request: Request,
    api_key: str = Depends(api_key_scheme),
) -> str:
    """验证 API 密钥，以短事务记录审计日志后再返回或拒绝请求。"""
    config_service: ConfigService = get_config_service(request)
    trusted_proxies_str = await config_service.get("trustedProxies", "")
    trusted_networks = []
    if trusted_proxies_str:
        for proxy_entry in trusted_proxies_str.split(','):
            try:
                trusted_networks.append(ipaddress.ip_network(proxy_entry.strip()))
            except ValueError:
                logger.warning(f"无效的受信任代理IP或CIDR: '{proxy_entry.strip()}'，已忽略。")

    client_ip_str = normalize_ip(request.client.host if request.client else "127.0.0.1")
    is_trusted = False
    if trusted_networks:
        try:
            client_addr = ipaddress.ip_address(client_ip_str)
            is_trusted = any(client_addr in network for network in trusted_networks)
        except ValueError:
            logger.warning(f"无法将客户端IP '{client_ip_str}' 解析为有效的IP地址。")

    if is_trusted:
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            client_ip_str = normalize_ip(x_forwarded_for.split(',')[0].strip())
        else:
            client_ip_str = normalize_ip(request.headers.get("x-real-ip", client_ip_str))

    # 保留请求大小限制和脱敏，审计日志不得直接保存原始凭据。
    try:
        request_headers_str = build_audit_request_headers(request)
    except Exception:
        request_headers_str = None
    try:
        request_body_str = await capture_audit_request_body(request)
    except Exception:
        request_body_str = None

    if not api_key:
        api_key = request.headers.get("x-api-key")

    error_detail = None
    if not api_key:
        message = "API Key缺失"
        error_detail = "Not authenticated: API Key is missing."
    else:
        stored_key = await config_service.get("externalApiKey", "")
        if not stored_key or not secrets.compare_digest(api_key, stored_key):
            message = "无效的API密钥"
            error_detail = message
        else:
            message = "API Key验证通过"

    status_code = status.HTTP_401_UNAUTHORIZED if error_detail else status.HTTP_200_OK
    db = get_database_service()
    # 先提交审计记录，再抛出鉴权异常，避免拒绝请求的日志被事务回滚。
    async with db.transaction():
        log_entry = await db.external_log.create(
            ip_address=client_ip_str,
            endpoint=request.url.path,
            status_code=status_code,
            message=message,
            request_headers=request_headers_str,
            request_body=request_body_str,
        )
        log_id = log_entry.id
    request.state.external_log_id = log_id
    if error_detail:
        raise HTTPException(status_code=status_code, detail=error_detail)
    return api_key

