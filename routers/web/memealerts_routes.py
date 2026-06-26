import datetime
from datetime import timedelta
from typing import Annotated, Any

import jwt
import sqlalchemy as sa
from dateutil.tz import UTC
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, HTTPException
from fastapi.params import Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from config import settings, memealerts_scope
from container import Container
from database.models import MemealertsSettings
from dependencies import get_db
from routers.security_helpers import user_auth
from services.memes import MemealertsService

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
    db: Annotated[AsyncSession, Depends(get_db)],
    memealerts: Annotated[MemealertsService, Depends(Provide[Container.memealerts])],
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

    access_token, refresh_token, expires_at = await memealerts.get_user_access_refresh_tokens_by_authorization_code(authorization_code=code)
    await db.execute(
        sa.update(MemealertsSettings)
        .where(MemealertsSettings.user_id == user.id)
        .values(
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
            token_created_at=sa.func.now(),
            token_scopes=" ".join(sp.value for sp in memealerts_scope)
        )
    )
    await db.commit()
    return RedirectResponse(url="/panel")
