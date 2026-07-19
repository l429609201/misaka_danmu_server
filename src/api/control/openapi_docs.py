"""
外部控制 API 的独立 OpenAPI / Swagger UI 文档

why：从 main.py 抽离，main.py 只负责注册路由。为外部控制 API 生成仅含 API Key 鉴权的
独立 OpenAPI schema，并提供本地化 Swagger UI 页面。
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse


def build_control_api_openapi(app: FastAPI) -> dict:
    """生成仅包含外部控制 API 路由和 API Key 认证的独立 OpenAPI schema（带缓存）。"""
    from fastapi.openapi.utils import get_openapi

    if getattr(app, "_control_openapi_schema", None):
        return app._control_openapi_schema

    _doc_helper_paths = {"/api/control/openapi.json", "/api/control/docs"}
    control_routes = [
        route for route in app.routes
        if hasattr(route, 'path') and route.path.startswith('/api/control')
        and route.path not in _doc_helper_paths
    ]

    schema = get_openapi(
        title="Misaka Danmaku External Control API",
        version="1.0.0",
        # 固定为 3.0.3：FastAPI 默认生成 OAS 3.1.0，但项目内置的旧版 swagger-ui-bundle
        # 解析 3.1.0 的 paths 失败，导致文档页显示 "No operations defined in spec!"。
        openapi_version="3.0.3",
        description="用于外部自动化和集成的API。支持两种鉴权方式：\n"
                    "1. **查询参数**：`?api_key=<你的密钥>`\n"
                    "2. **请求头**：`X-API-KEY: <你的密钥>`（推荐，也用于 MCP 连接）",
        routes=control_routes,
    )

    # 替换安全方案：同时支持查询参数和请求头
    schema["components"] = schema.get("components", {})
    schema["components"]["securitySchemes"] = {
        "APIKeyQuery": {
            "type": "apiKey", "in": "query", "name": "api_key",
            "description": "通过 URL 查询参数传递 API Key",
        },
        "APIKeyHeader": {
            "type": "apiKey", "in": "header", "name": "X-API-KEY",
            "description": "通过请求头传递 API Key（推荐，也用于 MCP 连接）",
        },
    }

    # 给所有路径添加 API Key 安全要求（两种方式任选其一）
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["security"] = [{"APIKeyQuery": []}, {"APIKeyHeader": []}]

    app._control_openapi_schema = schema
    return schema


def register_control_api_docs(app: FastAPI) -> None:
    """注册外部控制 API 的 openapi.json 与本地化 Swagger UI 文档路由。"""

    @app.get("/api/control/openapi.json", include_in_schema=False)
    async def control_api_openapi_json():
        """外部控制API的独立 OpenAPI JSON"""
        return JSONResponse(content=build_control_api_openapi(app))

    @app.get("/api/control/docs", include_in_schema=False)
    async def custom_swagger_ui_html() -> HTMLResponse:
        """提供一个使用本地静态资源、部分汉化的 Swagger UI 页面。"""
        from src.utils.swagger_cn import get_swagger_ui_html_cn
        return get_swagger_ui_html_cn(
            openapi_url="/api/control/openapi.json",
            title="Misaka Danmaku 外部控制 API 文档",
        )
