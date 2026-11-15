"""API ачивок: тихие репорты клиентских событий (пасхалка, обрыв звёздочки).

Ачивки не имеют пользовательского UI-флоя — эндпоинты принимают факт
события и молча записывают его (идемпотентно). Условия получения нигде
не афишируются.
"""

import logging
from typing import Annotated

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Security
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from container import Container
from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth
from services.achievements import AchievementsService
from services.cache import Cache
from utils.overlay_secret import ensure_overlay_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/achievements", tags=["User achievements"])

# Анти-спам репортов обрыва звёздочки: звёздочка восстанавливается и рвётся
# снова, ачивка одна — чаще одного репорта в 10 минут принимать незачем.
STAR_BREAK_WINDOW_S = 10 * 60


class StarBreakSchema(BaseModel):
    channel_id: int = Field(ge=1)


@router.post("/easter-egg")
@inject
async def easter_egg(
    user: Annotated[User, Security(user_auth)],
    achievements: Annotated[AchievementsService, Depends(Provide[Container.achievements])],
) -> dict[str, str]:
    """Отметить активацию пасхалки «сборщик точек» (``dots-easter-egg.js``).

    Идемпотентно: повторные вызовы ничего не меняют.
    """
    await achievements.unlock(user.twitch_id, "easter_dots")
    return {"status": "ok"}


@router.post("/star-break")
@inject
async def star_break(
    request: Request,
    payload: Annotated[StarBreakSchema, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
    achievements: Annotated[AchievementsService, Depends(Provide[Container.achievements])],
):
    """Репорт из оверлея «Звёздочка»: зритель сорвал звёздочку (``star.js``).

    Вызывается только при обрыве по heat-клику зрителя (клики стримера
    в OBS не считаются). Без user_auth — вместо неё overlay_secret через
    заголовок ``X-Overlay-Secret`` (как ``/api/user/overlay/custom-commands``).
    """
    secret_header = request.headers.get("X-Overlay-Secret")
    if not secret_header:
        raise HTTPException(401, "No overlay secret provided")

    user = (await db.execute(sa.select(User).where(User.twitch_id == str(payload.channel_id)))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    secret = await ensure_overlay_secret(db, user)
    if secret_header != str(secret):
        raise HTTPException(403, "Invalid overlay secret")

    if not await cache.check_rate_limit(f"ach_star_break:{payload.channel_id}", limit=1, window_s=STAR_BREAK_WINDOW_S):
        return JSONResponse({"status": "rate_limited"}, 200)

    await achievements.unlock(user.twitch_id, "star_torn")
    return {"status": "ok"}
