from fastapi import APIRouter

from .ui_api import router as ui_router, auth_router
from .bangumi_api import router as bangumi_router
from .tmdb_api import router as tmdb_router
from .webhook_api import router as webhook_router
from .imdb_api import router as imdb_router
from .tvdb_api import router as tvdb_router
from .douban_api import router as douban_router
from .control_api import router as control_router

# This router aggregates all non-dandanplay API endpoints.
api_router = APIRouter()

api_router.include_router(ui_router, prefix="/ui", tags=["Web UI API"], include_in_schema=False)
api_router.include_router(auth_router, prefix="/ui/auth", tags=["Auth"], include_in_schema=False)
api_router.include_router(bangumi_router, prefix="/bgm", tags=["Bangumi"], include_in_schema=False)
api_router.include_router(tmdb_router, prefix="/tmdb", tags=["TMDB"], include_in_schema=False)
api_router.include_router(douban_router, prefix="/douban", tags=["Douban"], include_in_schema=False)
api_router.include_router(imdb_router, prefix="/imdb", tags=["IMDb"], include_in_schema=False)
api_router.include_router(tvdb_router, prefix="/tvdb", tags=["TVDB"], include_in_schema=False)
api_router.include_router(webhook_router, prefix="/webhook", tags=["Webhook"], include_in_schema=False)
api_router.include_router(control_router, prefix="/control", tags=["External Control API"])

# Note: The dandan_router is handled separately in main.py because its
# path structure (/api/{token}) is different and needs to be at the root
# of the /api prefix, while these other routers are nested under it
# (e.g., /api/ui, /api/tmdb).