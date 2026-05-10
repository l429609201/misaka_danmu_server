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

import logging
import secrets

from fastapi import FastAPI, Depends, HTTPException, Request, status

logger = logging.getLogger(__name__)


async def _verify_mcp_api_key(request: Request) -> None:
    """
    MCP 认证依赖：验证 X-API-KEY 请求头或 apikey 查询参数。
    复用 externalApiKey 配置值，与外部控制 API 共享密钥。

    支持两种认证方式（与 MoviePilot 一致）：
    1. 请求头: X-API-KEY: <API_TOKEN>
    2. 查询参数: ?apikey=<API_TOKEN>
    """
    # 从请求头或查询参数中获取 API Key
    api_key = request.headers.get("x-api-key") or request.query_params.get("apikey")

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP 认证失败: 缺少 API Key。请通过 X-API-KEY 请求头或 ?apikey= 查询参数提供。",
        )

    # 从配置管理器获取存储的 API Key
    config_manager = request.app.state.config_manager
    stored_key = await config_manager.get("externalApiKey", "")

    if not stored_key or not secrets.compare_digest(api_key, stored_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP 认证失败: 无效的 API Key。",
        )


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
        )

        # 挂载 MCP 端点到 /api/mcp，使用 streamable-http 传输
        mcp.mount_http(app, mount_path="/api/mcp")

        logger.info("MCP Server 已挂载到 /api/mcp (streamable-http)")
        logger.info(
            "客户端连接示例: "
            '{"type": "http", "url": "http://<host>:7768/api/mcp", '
            '"headers": {"X-API-KEY": "<externalApiKey>"}}'
        )

    except Exception as e:
        logger.error(f"MCP Server 初始化失败: {e}")
        logger.exception("MCP 初始化详细错误:")
