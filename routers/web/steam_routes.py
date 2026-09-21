"""Steam OpenID-авторизация.

Флоу:
- ``GET /auth/steam`` — редирект на Steam OpenID (только для залогиненных).
- ``GET /auth/steam/callback`` — приём OpenID-ответа, верификация через
  ``check_authentication``, подтягивание ``profileurl`` через GetPlayerSummaries,
  сохранение ``steam_id`` + ``steam_profile_url`` в БД, редирект на ``/panel``.

Отвязка Steam-аккаунта (POST /api/user/steam/unlink) — см. ``routers/api/user/steam.py``.
"""

import logging
from typing import Annotated, Any

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import RedirectResponse

from container import Container
from dependencies import get_db
from routers.security_helpers import user_auth
from services.steam import SteamService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["Service"])


@router.get("/auth/steam", response_class=RedirectResponse)
@inject
async def steam_auth(
    steam: Annotated[SteamService, Depends(Provide[Container.steam_service])],
    user: Any = Security(user_auth),
):
    """Инициировать OpenID-логин через Steam — редирект на steamcommunity.com."""
    return RedirectResponse(steam.build_auth_url())


@router.get("/auth/steam/callback", response_class=RedirectResponse)
@inject
async def steam_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    steam: Annotated[SteamService, Depends(Provide[Container.steam_service])],
    user: Any = Security(user_auth),
):
    """Приём OpenID-ответа от Steam: верификация, подтягивание profileurl, сохранение."""
    # Собираем все openid.* параметры из query string в плоский dict.
    params: dict[str, str] = {k: v for k, v in request.query_params.multi_items() if k.startswith("openid.")}
    if not params:
        logger.warning("Steam callback: no openid.* params in query string")
        return RedirectResponse(url="/panel")

    steam_id = await steam.verify_openid_response(params)
    if not steam_id:
        logger.warning("Steam OpenID verification failed for user_id=%s", getattr(user, "id", None))
        return RedirectResponse(url="/panel")

    profile_url = await steam.fetch_profile_url(steam_id)
    if not profile_url:
        logger.warning("Steam GetPlayerSummaries returned no profileurl for steam_id=%s", steam_id)
        # Даже без profileurl сохраняем steam_id — профиль мог быть скрыт/пуст.
        # profileurl опционально дополняется позже, но пока сохраняем только то, что есть.

    user.links.steam_id = steam_id
    if profile_url:
        user.links.steam = profile_url
    await db.commit()
    logger.info("Steam linked: user_id=%s steam_id=%s", user.id, steam_id)
    return RedirectResponse(url="/panel")
