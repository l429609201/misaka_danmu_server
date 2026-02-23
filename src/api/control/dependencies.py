"""
外部控制API的依赖注入函数
"""

import re
import json
import logging
import secrets
import ipaddress

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyQuery
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, get_db_session, ConfigManager
from src.services import ScraperManager, TaskManager, SchedulerManager, MetadataSourceManager
from src.rate_limiter import RateLimiter
from src.ai import AIMatcherManager

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


def get_metadata_manager(request: Request) -> MetadataSourceManager:
    """依赖项：从应用状态获取元数据源管理器"""
    return request.app.state.metadata_manager


def get_task_manager(request: Request) -> TaskManager:
    """依赖项：从应用状态获取任务管理器"""
    return request.app.state.task_manager


def get_scheduler_manager(request: Request) -> SchedulerManager:
    """依赖项：从应用状态获取定时任务调度器"""
    return request.app.state.scheduler_manager


def get_config_manager(request: Request) -> ConfigManager:
    """依赖项：从应用状态获取配置管理器"""
    return request.app.state.config_manager


def get_rate_limiter(request: Request) -> RateLimiter:
    """依赖项：从应用状态获取速率限制器"""
    return request.app.state.rate_limiter


def get_ai_matcher_manager(request: Request) -> AIMatcherManager:
    """依赖项：从应用状态获取AI匹配器管理器"""
    return request.app.state.ai_matcher_manager


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
    session: AsyncSession = Depends(get_db_session),
) -> str:
    """依赖项：验证API密钥并记录请求。如果验证成功，返回 API Key。"""
    # --- 解析真实客户端IP，支持CIDR ---
    config_manager: ConfigManager = request.app.state.config_manager
    trusted_proxies_str = await config_manager.get("trustedProxies", "")
    trusted_networks = []
    if trusted_proxies_str:
        for proxy_entry in trusted_proxies_str.split(','):
            try:
                trusted_networks.append(ipaddress.ip_network(proxy_entry.strip()))
            except ValueError:
                logger.warning(f"无效的受信任代理IP或CIDR: '{proxy_entry.strip()}'，已忽略。")

    client_ip_str = request.client.host if request.client else "127.0.0.1"
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
            client_ip_str = x_forwarded_for.split(',')[0].strip()
        else:
            client_ip_str = request.headers.get("x-real-ip", client_ip_str)
    # --- IP解析结束 ---

    endpoint = request.url.path

    # --- 捕获请求头和请求体 ---
    try:
        # 过滤敏感头信息，保留有用的请求头
        filtered_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ('authorization', 'cookie')
        }
        # 从查询参数中移除 api_key
        query_str = str(request.url.query) if request.url.query else ""
        if query_str:
            import re as _re
            query_str = _re.sub(r'api_key=[^&]*', 'api_key=***', query_str)
            filtered_headers['_query'] = query_str
        request_headers_str = json.dumps(filtered_headers, ensure_ascii=False, indent=2)
    except Exception:
        request_headers_str = None

    try:
        raw_body = await request.body()
        request_body_str = raw_body.decode(errors='ignore') if raw_body else None
        # 将body放回请求流，确保后续端点能正常读取
        if raw_body:
            async def receive():
                return {"type": "http.request", "body": raw_body}
            request._receive = receive
    except Exception:
        request_body_str = None

    if not api_key:
        log_entry = await crud.create_external_api_log(
            session, client_ip_str, endpoint, status.HTTP_401_UNAUTHORIZED, "API Key缺失",
            request_headers=request_headers_str,
            request_body=request_body_str,
        )
        request.state.external_log_id = log_entry.id
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated: API Key is missing.",
        )

    stored_key = await config_manager.get("externalApiKey", "")

    if not stored_key or not secrets.compare_digest(api_key, stored_key):
        log_entry = await crud.create_external_api_log(
            session, client_ip_str, endpoint, status.HTTP_401_UNAUTHORIZED, "无效的API密钥",
            request_headers=request_headers_str,
            request_body=request_body_str,
        )
        request.state.external_log_id = log_entry.id
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的API密钥"
        )
    # 记录成功的API Key验证
    log_entry = await crud.create_external_api_log(
        session, client_ip_str, endpoint, status.HTTP_200_OK, "API Key验证通过",
        request_headers=request_headers_str,
        request_body=request_body_str,
    )
    request.state.external_log_id = log_entry.id
    return api_key

