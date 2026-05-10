"""
HTTP 中间件模块

将响应捕获等中间件从 main.py 独立出来，避免 main.py 过度膨胀。
包含：
- capture_api_response: 统一捕获 外部控制/MCP/Token API 的响应头和响应体
- log_not_found_requests: 404 路径保护（API 路径返回 403 防枚举）
"""

import json
import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.responses import Response as StarletteResponse

from src.db import crud

logger = logging.getLogger(__name__)

# 需要捕获响应的路径前缀
_CAPTURE_PREFIXES = ("/api/control/", "/api/mcp/", "/api/v1/")

# 404→403 保护不应拦截的路径（MCP 子应用由 fastapi-mcp 挂载，路由机制不同）
_SKIP_404_PREFIXES = ("/api/mcp",)


async def capture_api_response(request: Request, call_next):
    """
    中间件：统一捕获 API 响应头和响应体，更新到对应的访问日志中。
    支持三种路径：
    - /api/control/ → external_api_logs (通过 external_log_id)
    - /api/mcp/     → external_api_logs (通过 external_log_id)
    - /api/v1/      → token_access_logs (通过 token_log_id)
    """
    path = request.url.path
    if not any(path.startswith(prefix) for prefix in _CAPTURE_PREFIXES):
        return await call_next(request)

    response = await call_next(request)

    # 判断该更新哪个日志表
    external_log_id = getattr(request.state, 'external_log_id', None)
    token_log_id = getattr(request.state, 'token_log_id', None)

    if external_log_id is None and token_log_id is None:
        return response

    try:
        # 读取响应体（需要消费流并重建响应）
        response_body_bytes = b""
        async for chunk in response.body_iterator:
            response_body_bytes += chunk

        response_headers_str = json.dumps(dict(response.headers), ensure_ascii=False, indent=2)
        response_body_str = response_body_bytes.decode(errors='ignore') if response_body_bytes else None

        # 截断过长的响应体
        max_body_len = 10000
        if response_body_str and len(response_body_str) > max_body_len:
            response_body_str = response_body_str[:max_body_len] + f"\n... (已截断，总长度: {len(response_body_bytes)} 字节)"

        # 更新对应的日志表
        if external_log_id:
            session_factory = request.app.state.db_session_factory
            async with session_factory() as session:
                await crud.update_external_api_log_response(
                    session,
                    log_id=external_log_id,
                    status_code=response.status_code,
                    response_headers=response_headers_str,
                    response_body=response_body_str,
                )
        elif token_log_id:
            await crud.update_token_access_log_response(
                log_id=token_log_id,
                status_code=response.status_code,
                response_headers=response_headers_str,
                response_body=response_body_str,
            )

        # 重建响应
        return StarletteResponse(
            content=response_body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
    except Exception as e:
        logger.debug(f"捕获API响应信息失败: {e}")
        try:
            return StarletteResponse(
                content=response_body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        except Exception:
            return response


async def log_not_found_requests(request: Request, call_next):
    """
    中间件：捕获所有请求。
    - 如果是未找到的API路径 (404)，则返回 403 Forbidden，避免路径枚举。
    - 对其他 404 错误，记录详细信息以供调试。
    """
    response = await call_next(request)
    if response.status_code == 404:
        if request.url.path.startswith("/api/"):
            # MCP 等子应用路径不做 404→403 转换
            if any(request.url.path.startswith(p) for p in _SKIP_404_PREFIXES):
                return response
            original_body_text = None
            try:
                body_bytes = getattr(response, "body", b"")
                if isinstance(body_bytes, (bytes, bytearray)) and body_bytes:
                    try:
                        original_json = json.loads(body_bytes)
                        original_body_text = json.dumps(original_json, ensure_ascii=False)
                    except Exception:
                        original_body_text = body_bytes.decode("utf-8", "ignore")
            except Exception as e:
                logger.debug(f"读取原始404响应body失败: {e}")

            if original_body_text:
                logger.warning("API路径未找到原始响应内容: %s", original_body_text)

            logger.warning(
                f"API路径未找到 (返回403): {request.method} {request.url.path} from {request.client.host}"
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Forbidden"}
            )

        scope = request.scope
        serializable_scope = {
            "type": scope.get("type"),
            "http_version": scope.get("http_version"),
            "server": scope.get("server"),
            "client": scope.get("client"),
            "scheme": scope.get("scheme"),
            "method": scope.get("method"),
            "root_path": scope.get("root_path"),
            "path": scope.get("path"),
            "raw_path": scope.get("raw_path", b"").decode("utf-8", "ignore"),
            "query_string": scope.get("query_string", b"").decode("utf-8", "ignore"),
            "headers": {h[0].decode("utf-8", "ignore"): h[1].decode("utf-8", "ignore") for h in scope.get("headers", [])},
        }
        log_details = {
            "message": "HTTP 404 Not Found - 未找到匹配的路由或文件",
            "url": str(request.url),
            "raw_request_scope": serializable_scope
        }
        logger.warning("未处理的请求详情 (原始请求范围):\n%s", json.dumps(log_details, indent=2, ensure_ascii=False))
    return response
