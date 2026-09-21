"""API-эндпоинты для управления привязкой Steam-аккаунта.

Префикс ``/steam`` подключается в ``routers/api/user_api.py`` через
``user_api_router`` (префикс ``/user``) и ``api_router`` (префикс ``/api``),
итоговый путь — ``/api/user/steam/<route>``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth

router = APIRouter(prefix="/steam", tags=["Steam"])


@router.post("/unlink", response_class=JSONResponse)
async def steam_unlink(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User = Security(user_auth),
):
    """Отвязать Steam-аккаунт от пользователя — обнулить steam_id и steam в links."""
    user.links.steam_id = None  # type: ignore[assignment]
    user.links.steam = None  # type: ignore[assignment]
    await db.commit()
    return JSONResponse({"title": "Готово", "message": "Steam-аккаунт отвязан."}, 200)
