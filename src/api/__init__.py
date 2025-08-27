from fastapi import APIRouter

from .ui_api import router as ui_router, auth_router
from .webhook_api import router as webhook_router
from .control_api import router as control_router

# This router aggregates all non-dandanplay API endpoints.
api_router = APIRouter()

# The ui_router contains core UI functionalities.
api_router.include_router(ui_router, prefix="/ui", tags=["Web UI API"], include_in_schema=False)
api_router.include_router(auth_router, prefix="/ui/auth", tags=["Auth"], include_in_schema=False)

api_router.include_router(webhook_router, prefix="/webhook", tags=["Webhook"], include_in_schema=False)

# 注意：dandan_router 在 main.py 中被单独处理，因为它的路径结构
# (/api/v1/{token}) 与其他嵌套在 /api 前缀下的路由 (例如 /api/ui, /api/tmdb) 不同，
# 需要位于 /api 前缀的根部。