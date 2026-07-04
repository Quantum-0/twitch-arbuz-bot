import datetime
from datetime import timedelta
from typing import Annotated, Any

import jwt
from dateutil.tz import UTC
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, HTTPException
from fastapi.params import Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from config import settings
from container import Container
from dependencies import get_db
from routers.security_helpers import user_auth
from services.memes_v2 import MemealertsOAuthService

router = APIRouter(prefix="/memealerts", tags=["Service"])


@router.get("/auth", response_class=RedirectResponse)
async def memealerts_auth(
    user: Any = Security(user_auth),
):
    payload = {
        "user_id": user.id,
        "iat": datetime.datetime.now(tz=UTC),
        "exp": datetime.datetime.now(tz=UTC) + timedelta(minutes=5),
    }
    state = jwt.encode(payload, settings.memealerts_state_secret.get_secret_value())
    url = settings.memealerts_oauth_url.replace("{state}", state)
    return RedirectResponse(url)


@router.get("/callback", response_class=RedirectResponse)
@inject
async def callback(
    memealerts: Annotated[MemealertsOAuthService, Depends(Provide[Container.memealerts_auth])],
    code: str,
    state: str,
    user: Any = Security(user_auth),
):
    try:
        decoded = jwt.decode(state, settings.memealerts_state_secret.get_secret_value(), algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        raise HTTPException(403, detail="Signature has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(403, detail="Invalid `state` value")
    if decoded.get("user_id") != user.id:
        raise HTTPException(403, detail="Invalid `state` value")

    tokens = await memealerts.auth_user(authorization_code=code, user=user)
    if not tokens:
        raise HTTPException(400, detail="Memealerts не вернул токен пользователя. Попробуйте ещё раз.")

    return RedirectResponse(url="/panel")
