"""
MCP (Model Context Protocol) Server 模块

参考 MoviePilot 的 MCP 实现，将外部控制 API 暴露为 MCP 工具。
支持 streamable-http 传输协议，认证方式与 MoviePilot 一致：
- 请求头: X-API-KEY
- 查询参数: ?apikey=

使用方式（客户端配置）:
{
    "mcpServers": {
        "misaka-danmu": {
            "type": "http",
            "url": "http://127.0.0.1:7768/api/mcp",
            "headers": {
                "X-API-KEY": "你的externalApiKey"
            }
        }
    }
}
"""

import json
import logging
import secrets
import ipaddress

from fastapi import FastAPI, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, get_db_session, ConfigManager

logger = logging.getLogger(__name__)


def _resolve_client_ip(request: Request, trusted_networks: list) -> str:
    """解析真实客户端 IP（支持反向代理和 CIDR 白名单）"""
    client_ip_str = request.client.host if request.client else "127.0.0.1"
    is_trusted = False
    if trusted_networks:
        try:
            client_addr = ipaddress.ip_address(client_ip_str)
            is_trusted = any(client_addr in network for network in trusted_networks)
        except ValueError:
            pass
    if is_trusted:
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            client_ip_str = x_forwarded_for.split(',')[0].strip()
        else:
            client_ip_str = request.headers.get("x-real-ip", client_ip_str)
    return client_ip_str


async def _verify_mcp_api_key(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """
    MCP 认证依赖：验证 X-API-KEY 请求头或 apikey 查询参数。
    复用 externalApiKey 配置值，与外部控制 API 共享密钥。
    同时记录访问日志到 external_api_logs 表（与外部控制 API 共用）。

    支持两种认证方式（与 MoviePilot 一致）：
    1. 请求头: X-API-KEY: <API_TOKEN>
    2. 查询参数: ?apikey=<API_TOKEN>
    """
    config_manager: ConfigManager = request.app.state.config_manager

    # --- 解析真实客户端 IP ---
    trusted_proxies_str = await config_manager.get("trustedProxies", "")
    trusted_networks = []
    if trusted_proxies_str:
        for proxy_entry in trusted_proxies_str.split(','):
            try:
                trusted_networks.append(ipaddress.ip_network(proxy_entry.strip()))
            except ValueError:
                pass
    client_ip_str = _resolve_client_ip(request, trusted_networks)
    endpoint = request.url.path

    # --- 捕获请求头（过滤敏感信息）---
    try:
        filtered_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ('authorization', 'cookie', 'x-api-key')
        }
        query_str = str(request.url.query) if request.url.query else ""
        if query_str:
            import re
            query_str = re.sub(r'apikey=[^&]*', 'apikey=***', query_str)
            filtered_headers['_query'] = query_str
        request_headers_str = json.dumps(filtered_headers, ensure_ascii=False, indent=2)
    except Exception:
        request_headers_str = None

    # --- 捕获请求体 ---
    try:
        raw_body = await request.body()
        request_body_str = raw_body.decode(errors='ignore') if raw_body else None
        # 截断过长的请求体
        if request_body_str and len(request_body_str) > 10000:
            request_body_str = request_body_str[:10000] + f"\n... (已截断，总长度: {len(raw_body)} 字节)"
        # 将 body 放回请求流，确保后续处理能正常读取
        if raw_body:
            async def receive():
                return {"type": "http.request", "body": raw_body}
            request._receive = receive
    except Exception:
        request_body_str = None

    # --- 认证逻辑 ---
    api_key = request.headers.get("x-api-key") or request.query_params.get("apikey")

    if not api_key:
        log_entry = await crud.create_external_api_log(
            session, client_ip_str, endpoint, status.HTTP_401_UNAUTHORIZED,
            "MCP: API Key缺失",
            request_headers=request_headers_str,
            request_body=request_body_str,
        )
        request.state.external_log_id = log_entry.id
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP 认证失败: 缺少 API Key。请通过 X-API-KEY 请求头或 ?apikey= 查询参数提供。",
        )

    stored_key = await config_manager.get("externalApiKey", "")

    if not stored_key or not secrets.compare_digest(api_key, stored_key):
        log_entry = await crud.create_external_api_log(
            session, client_ip_str, endpoint, status.HTTP_401_UNAUTHORIZED,
            "MCP: 无效的API密钥",
            request_headers=request_headers_str,
            request_body=request_body_str,
        )
        request.state.external_log_id = log_entry.id
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP 认证失败: 无效的 API Key。",
        )

    # 记录成功的认证
    log_entry = await crud.create_external_api_log(
        session, client_ip_str, endpoint, status.HTTP_200_OK,
        "MCP: API Key验证通过",
        request_headers=request_headers_str,
        request_body=request_body_str,
    )
    request.state.external_log_id = log_entry.id


def setup_mcp(app: FastAPI) -> None:
    """
    初始化并挂载 MCP Server 到 FastAPI 应用。

    - 只暴露 "External Control API" tag 下的路由作为 MCP 工具
    - 认证使用 externalApiKey，支持 X-API-KEY 请求头和 ?apikey= 查询参数
    - 挂载到 /api/mcp 路径，使用 streamable-http 传输
    """
    try:
        from fastapi_mcp import FastApiMCP, AuthConfig
    except ImportError:
        logger.warning(
            "fastapi-mcp 库未安装，MCP Server 功能不可用。"
            "请运行 `pip install fastapi-mcp` 安装。"
        )
        return

    try:
        mcp = FastApiMCP(
            app,
            name="Misaka Danmaku MCP Server",
            description=(
                "Misaka 弹幕库 MCP Server，提供弹幕搜索、导入、管理等外部控制能力。"
                "通过 MCP 协议，AI Agent 可以用自然语言调用弹幕库的各项功能。"
            ),
            # 只暴露外部控制 API
            include_tags=["External Control API"],
            # MCP 层面的认证
            auth_config=AuthConfig(
                dependencies=[Depends(_verify_mcp_api_key)],
            ),
            # 转发认证头，MCP 调用工具时会将 X-API-KEY 传递给 /api/control/ 的鉴权
            headers=["x-api-key"],
        )

        # 挂载 MCP 端点到 /api/mcp，使用 streamable-http 传输
        mcp.mount_http(app, mount_path="/api/mcp")

        logger.info("MCP Server 已挂载到 /api/mcp (streamable-http)")
        logger.info(
            "MCP 客户端连接配置: "
            '{"type": "http", "url": "http://<host>:7768/api/mcp", '
            '"headers": {"X-API-KEY": "<externalApiKey>"}}'
        )

    except Exception as e:
        logger.error(f"MCP Server 初始化失败: {e}")
        logger.exception("MCP 初始化详细错误:")
