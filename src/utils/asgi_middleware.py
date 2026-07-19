"""
纯 ASGI 中间件模块（传输层/框架层通用组件，与 proxy_middleware 同级）

why：原先 main.py 用两个 `@app.middleware("http")`（基于 Starlette BaseHTTPMiddleware）
包裹 404 保护与 API 响应捕获。BaseHTTPMiddleware 内部会起 anyio task group 跑下游，
客户端在响应完成前断开时，cancel scope 会级联取消整个下游调用链（含正在使用的 asyncpg/
aiomysql 连接），触发连接池 terminate 的二次异常刷屏（见 Starlette discussion #1527）。

改为纯 ASGI 中间件：直接 `await self.app(scope, receive, send)`，不套 task group，
客户端断开时不会凭空级联取消下游 DB 操作，从源头消除该类噪音；功能与原实现等价。
"""

import json
import logging

logger = logging.getLogger(__name__)

# 需要捕获响应的路径前缀（外部控制/MCP/Token API）
_CAPTURE_PREFIXES = ("/api/control/", "/api/mcp/", "/api/v1/")

# 404→403 保护不应拦截的路径（MCP 子应用路由机制不同；日历海报为公开懒加载图片，
# 无海报时返回 404/204 属正常，不应转 403）
_SKIP_404_PREFIXES = ("/api/mcp", "/api/ui/calendar/tmdb-poster")

# 捕获响应体用于写日志时的最大保留字节数
_MAX_BODY_LEN = 10000


class NotFoundGuardMiddleware:
    """404 路径保护（纯 ASGI）：API 路径的 404 转 403，避免路径枚举。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_api = path.startswith("/api/")
        skip = any(path.startswith(p) for p in _SKIP_404_PREFIXES)
        state = {"intercept": False, "buffer": []}

        async def send_wrapper(message):
            mtype = message["type"]
            if mtype == "http.response.start":
                status_code = message["status"]
                if status_code == 404 and is_api and not skip:
                    # 命中 API 404：吞掉原始 start，缓冲 body 以便记录原始内容后转 403
                    state["intercept"] = True
                    return
                if status_code == 404 and not is_api:
                    self._log_non_api_404(scope, path)
                await send(message)
                return
            if mtype == "http.response.body":
                if not state["intercept"]:
                    await send(message)
                    return
                state["buffer"].append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                await self._send_403(path, scope, b"".join(state["buffer"]), send)
                return
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _send_403(path, scope, original_body, send):
        """记录原始 404 内容并回送 403 Forbidden。"""
        try:
            if original_body:
                logger.warning("API路径未找到原始响应内容: %s", original_body.decode("utf-8", "ignore"))
        except Exception as e:
            logger.debug(f"读取原始404响应body失败: {e}")

        client = scope.get("client")
        client_host = client[0] if client else "unknown"
        logger.warning(f"API路径未找到 (返回403): {scope.get('method')} {path} from {client_host}")

        body = json.dumps({"detail": "Forbidden"}, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _log_non_api_404(scope, path):
        """非 API 路径的 404：记录简要请求信息以供调试。"""
        client = scope.get("client")
        logger.warning(
            "HTTP 404 Not Found - 未找到匹配的路由或文件: %s %s (client=%s)",
            scope.get("method"), path, client[0] if client else "unknown",
        )


class CaptureApiResponseMiddleware:
    """统一捕获外部控制/MCP/Token API 的响应头和响应体，更新到对应访问日志（纯 ASGI）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not any(path.startswith(p) for p in _CAPTURE_PREFIXES):
            await self.app(scope, receive, send)
            return

        # 确保 scope["state"] 为同一引用：下游路由 request.state 写入的 log_id
        # 才能在 self.app 返回后被本中间件读到（Starlette request.state 底层即 scope["state"]）
        scope.setdefault("state", {})

        cap = {"status": None, "headers": None, "body": bytearray(), "total": 0}

        async def send_wrapper(message):
            mtype = message["type"]
            if mtype == "http.response.start":
                cap["status"] = message["status"]
                cap["headers"] = message.get("headers", [])
            elif mtype == "http.response.body":
                body = message.get("body", b"")
                if body:
                    remain = (_MAX_BODY_LEN + 1) - len(cap["body"])
                    if remain > 0:
                        cap["body"].extend(body[:remain])
                    cap["total"] += len(body)
            await send(message)  # 始终透传，不影响客户端

        # 纯 ASGI：直接调用下游，不套 task group，避免客户端断开级联取消 DB 操作
        await self.app(scope, receive, send_wrapper)

        # app 完成后读取路由处理器写入的日志 id（request.state 底层即 scope["state"]）
        await self._persist_log(scope, cap)

    @staticmethod
    async def _persist_log(scope, cap):
        """把捕获的响应头/体更新到对应的访问日志表。失败仅降级为 debug，不影响主流程。"""
        state = scope.get("state") or {}
        external_log_id = state.get("external_log_id")
        token_log_id = state.get("token_log_id")
        if external_log_id is None and token_log_id is None:
            return

        try:
            from src.db import crud

            headers_dict = {
                k.decode("latin-1"): v.decode("latin-1")
                for k, v in (cap["headers"] or [])
            }
            headers_str = json.dumps(headers_dict, ensure_ascii=False, indent=2)
            body_str = bytes(cap["body"]).decode(errors="ignore") if cap["body"] else None
            if body_str and cap["total"] > _MAX_BODY_LEN:
                body_str = body_str[:_MAX_BODY_LEN] + f"\n... (已截断，总长度: {cap['total']} 字节)"

            app = scope.get("app")
            session_factory = app.state.db_session_factory if app else None

            if external_log_id and session_factory:
                async with session_factory() as session:
                    await crud.update_external_api_log_response(
                        session, log_id=external_log_id, status_code=cap["status"],
                        response_headers=headers_str, response_body=body_str,
                    )
            elif token_log_id:
                await crud.update_token_access_log_response(
                    log_id=token_log_id, status_code=cap["status"],
                    response_headers=headers_str, response_body=body_str,
                )
        except Exception as e:
            logger.debug(f"捕获API响应信息失败: {e}")
