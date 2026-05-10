"""
弹弹Play 兼容 API 的路由处理器

使用方式:
    from src.api.dandan.route_handler import DandanApiRoute, get_token_from_path
"""

import re
import logging
import ipaddress
from typing import Callable

from fastapi import HTTPException, Path, Request, Response, status, Depends
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, get_db_session, ConfigManager
from src.core import get_now, get_app_timezone

logger = logging.getLogger(__name__)


def _normalize_ip(ip_str: str) -> str:
    """标准化 IP 地址：将 IPv4-mapped IPv6（::ffff:x.x.x.x）还原为纯 IPv4"""
    try:
        addr = ipaddress.ip_address(ip_str)
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
    except ValueError:
        pass
    return ip_str


class DandanApiRoute(APIRoute):
    """
    自定义的 APIRoute 类，用于为 dandanplay 兼容接口定制异常处理。
    捕获 HTTPException，并以 dandanplay API v2 的格式返回错误信息。
    """
    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            except HTTPException as exc:
                # 简单的 HTTP 状态码到 dandanplay 错误码的映射
                # 1001: 无效的参数
                # 1003: 未授权或资源不可用
                # 404: 未找到
                # 500: 服务器内部错误
                error_code_map = {
                    status.HTTP_400_BAD_REQUEST: 1001,
                    status.HTTP_422_UNPROCESSABLE_ENTITY: 1001,
                    # 新增：将404也映射到1003，对外统一表现为"资源不可用"
                    status.HTTP_404_NOT_FOUND: 1003,
                    status.HTTP_403_FORBIDDEN: 1003,
                    status.HTTP_500_INTERNAL_SERVER_ERROR: 500,
                }
                error_code = error_code_map.get(exc.status_code, 1003)  # 默认客户端错误为1003

                # 为常见的错误代码提供更统一的错误消息
                error_message = "请求的资源不可用或您没有权限访问。" if error_code == 1003 else exc.detail

                # 始终返回 200 OK，错误信息在 JSON body 中体现
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={
                        "success": False,
                        "errorCode": error_code,
                        "errorMessage": error_message,
                    },
                )
        return custom_route_handler


async def get_token_from_path(
    request: Request,
    token: str = Path(..., description="路径中的API授权令牌"),
    session: AsyncSession = Depends(get_db_session),
):
    """
    一个 FastAPI 依赖项，用于验证路径中的 token。
    这是为 dandanplay 客户端设计的特殊鉴权方式。
    此函数现在还负责UA过滤和访问日志记录。
    """
    # --- 新增：解析真实客户端IP ---
    # --- 新增：解析真实客户端IP，支持CIDR ---
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
    client_ip_str = _normalize_ip(client_ip_str)  # ::ffff:x.x.x.x → x.x.x.x
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
            client_ip_str = _normalize_ip(x_forwarded_for.split(',')[0].strip())
        else:
            client_ip_str = _normalize_ip(request.headers.get("x-real-ip", client_ip_str))
    # --- IP解析结束 ---

    # 1. 验证 token 是否存在、启用且未过期
    request_path = request.url.path
    log_path = re.sub(r'^/api/v1/[^/]+', '', request_path)  # 从路径中移除 /api/v1/{token} 部分

    token_info = await crud.validate_api_token(session, token=token)
    if not token_info: 
        # 尝试记录失败的访问
        token_record = await crud.get_api_token_by_token_str(session, token)
        if token_record:
            expires_at = token_record.get('expiresAt')
            is_expired = False
            if expires_at:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=get_app_timezone())
                is_expired = expires_at < get_now()
            status_to_log = 'denied_expired' if is_expired else 'denied_disabled'
            crud.create_token_access_log(session, token_record['id'], client_ip_str, request.headers.get("user-agent"), log_status=status_to_log, path=log_path)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API token")

    # 2. UA 过滤
    ua_filter_mode = await crud.get_config_value(session, 'uaFilterMode', 'off')
    user_agent = request.headers.get("user-agent", "")

    if ua_filter_mode != 'off':
        ua_rules = await crud.get_ua_rules(session)
        ua_list = [rule['uaString'] for rule in ua_rules]
        
        is_matched = any(rule in user_agent for rule in ua_list)

        if ua_filter_mode == 'blacklist' and is_matched:
            crud.create_token_access_log(session, token_info['id'], client_ip_str, user_agent, log_status='denied_ua_blacklist', path=log_path)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User-Agent is blacklisted")

        if ua_filter_mode == 'whitelist' and not is_matched:
            crud.create_token_access_log(session, token_info['id'], client_ip_str, user_agent, log_status='denied_ua_whitelist', path=log_path)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User-Agent not in whitelist")

    # 3. 增加调用计数（后台异步执行，不阻塞请求）
    await crud.increment_token_call_count(session, token_info['id'])

    # 4. 记录成功访问（含请求头、请求体和方法）
    # 跳过高频轮询接口（taskcomment），避免日志刷屏
    _skip_log_paths = ('/taskcomment/', '/api/v2/taskcomment/')
    should_log = not any(skip in log_path for skip in _skip_log_paths)

    if should_log:
        # 捕获请求头（过滤敏感信息）
        request_headers_str = None
        try:
            filtered_headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in ('authorization', 'cookie')
            }
            import json as _json
            request_headers_str = _json.dumps(filtered_headers, ensure_ascii=False, indent=2)
        except Exception:
            pass

        request_body_str = None
        try:
            if request.method in ("POST", "PUT", "PATCH"):
                body_bytes = await request.body()
                if body_bytes and len(body_bytes) < 10000:  # 限制10KB
                    request_body_str = body_bytes.decode("utf-8", "ignore")
        except Exception:
            pass

        # 使用 awaited 版本获取 log_id，供中间件回填响应
        log_id = await crud.create_token_access_log_awaited(
            token_info['id'], client_ip_str, user_agent,
            log_status='allowed', path=log_path,
            method=request.method,
            request_headers=request_headers_str,
            request_body=request_body_str,
        )
        request.state.token_log_id = log_id

    return token

