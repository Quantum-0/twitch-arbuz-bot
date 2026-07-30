import datetime
import logging
from collections.abc import Callable
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

from config import settings
from container import Container
from database.models import MemealertsSettings, TwitchUserSettings
from routers.security_helpers import user_auth
from schemas.memealerts import MAChannel
from services.memes_v2 import MemealertsOAuthService, MemealertsV2Service
from twitch.client.twitch import Twitch

logger = logging.getLogger(__name__)

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
    memealerts_v2: Annotated[MemealertsV2Service, Depends(Provide[Container.memealerts_v2])],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    db_session_factory: Annotated[Callable[[], AsyncSession], Depends(Provide[Container.db_session_factory])],
    code: str,
    state: str,
    user: Any = Security(user_auth),
):
    try:
        decoded = jwt.decode(state, settings.memealerts_state_secret.get_secret_value(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(403, detail="Signature has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(403, detail="Invalid `state` value") from exc
    if decoded.get("user_id") != user.id:
        raise HTTPException(403, detail="Invalid `state` value")

    tokens = await memealerts.auth_user(authorization_code=code, user=user)
    if not tokens:
        raise HTTPException(400, detail="Memealerts не вернул токен пользователя. Попробуйте ещё раз.")

    try:
        ma_user = await memealerts_v2.get_user_info(tokens)
    except Exception:
        logger.warning("Не удалось получить информацию о канале Memealerts после авторизации", exc_info=True)
        ma_user = None

    if ma_user is not None and ma_user.channel is not None:
        await _store_channel_info(db_session_factory, user.id, ma_user.channel)

    if user.memealerts.memealerts_reward:
        try:
            await twitch.update_reward(
                user,
                user.memealerts.memealerts_reward,
                is_enabled=True,
                is_user_input_required=True,
                should_redemptions_skip_request_queue=False,
            )
        except Exception:
            logger.warning("Не удалось включить награду при подключении Memealerts", exc_info=True)

    return RedirectResponse(url="/panel")


async def _store_channel_info(
    db_session_factory: Callable[[], AsyncSession],
    user_id: int,
    channel: MAChannel,
) -> None:
    """Сохраняет unique_link и склонения валюты канала Memealerts в БД."""
    ma_values: dict[str, str | None] = {}
    decl = channel.currency_name_declensions
    if decl is not None:
        ma_values["memecoin_name_genitive"] = decl.genitive
        ma_values["memecoin_name_accusative"] = decl.accusative
        if decl.multiple is not None:
            ma_values["memecoin_name_genitive_multiple"] = decl.multiple.genitive
            ma_values["memecoin_name_accusative_multiple"] = decl.multiple.accusative

    async with db_session_factory() as db:
        if ma_values:
            await db.execute(
                sa.update(MemealertsSettings).where(MemealertsSettings.user_id == user_id).values(**ma_values)
            )
        if channel.unique_link is not None:
            await db.execute(
                sa.update(TwitchUserSettings)
                .where(TwitchUserSettings.user_id == user_id)
                .values(memealerts_link=channel.unique_link)
            )
        await db.commit()
