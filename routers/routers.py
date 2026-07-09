from fastapi import APIRouter

from routers.api.admin_api import router as admin_api_router
from routers.api.twitch_eventsub import router as eventsub_router
from routers.api.user_api import router as user_api_router
from routers.api.slovotron_webhook import router as slovotron_api_router
from routers.web.overlays import router as overlays_router
from routers.web.pages import router as pages_routers
from routers.web.service_routes import router as service_routes_router
from routers.web.memealerts_routes import router as memealerts_router
from routers.sse import router as sse_router
from routers.web.file_storage import router as files_router
from routers.web.obs_dock import router as obs_dock_router
from routers.robots.for_robots import router as router_for_robots  # noqa
from routers.web.extension import router as web_extension_router
from routers.api.extension import router as api_extension_router

# API
api_router = APIRouter(prefix="/api", tags=["API"])
api_router.include_router(eventsub_router, tags=["Twitch"])
api_router.include_router(user_api_router, tags=["User"])
api_router.include_router(admin_api_router, tags=["Admin"])
api_router.include_router(slovotron_api_router, tags=["Slovotron"])
api_router.include_router(api_extension_router, tags=["Extension"])

# User
user_router = APIRouter(prefix="", tags=["User"])
user_router.include_router(sse_router)
user_router.include_router(overlays_router)
user_router.include_router(pages_routers)
user_router.include_router(service_routes_router)
user_router.include_router(files_router)
user_router.include_router(obs_dock_router)
user_router.include_router(memealerts_router)
user_router.include_router(web_extension_router)