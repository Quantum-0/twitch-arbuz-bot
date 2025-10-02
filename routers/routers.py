from fastapi import APIRouter

from routers.api.twitch_eventsub import router as eventsub_router
from routers.api.user_api import router as user_api_router
from routers.api.admin_api import router as admin_api_router
from routers.frontend import router as frontend_router

api_router = APIRouter(prefix="/api", tags=["API"])
api_router.include_router(eventsub_router, tags=["Twitch"])
api_router.include_router(user_api_router, tags=["User"])
api_router.include_router(admin_api_router, tags=["Admin"])

user_router = APIRouter(prefix="", tags=["User"])
user_router.include_router(frontend_router)
