import asyncio
import hashlib
import hmac
import logging
import secrets
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from opentelemetry import trace
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from starlette import status
from starlette.requests import Request

from config import settings
from container_runtime import get_container
from database.database import AsyncSessionLocal
from database.models import User
from dependencies import get_db
from exceptions import UserBannedError
from services.bans import BanService
from services.follow_check import refresh_followed_to_admin
from utils.staff import is_staff

INTERACTION_UPDATE_INTERVAL_SECONDS = 15
INTERACTION_UPDATE_LOCK_TIMEOUT_MS = 50

# Login создателя бота — для него скипаем проверку зафолловленности (отладка).
ADMIN_LOGIN = "quantum075"

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def _get_bans_service() -> BanService | None:
    """BanService без требования инициализации контейнера ( юнит-тесты без DI)."""
    try:
        return get_container().bans()
    except Exception:
        return None


async def _ensure_not_banned(user: User) -> None:
    """Редирект-блок забаненного стримера (global-бан с фичей ``site``).

    Вызывается из user_auth / user_auth_optional после загрузки пользователя.
    HTML-запросы обработчик в main.py редиректит на /banned, API получают 403.
    """
    bans = _get_bans_service()
    if bans is None:
        return
    try:
        if await bans.is_site_banned(user.twitch_id):
            raise UserBannedError(user.twitch_id)
    except UserBannedError:
        raise
    except Exception:
        # Проверка бана не должна валить авторизацию целиком (Redis/БД сбои).
        logger.exception("Ban check failed for user %s, allowing request", user.twitch_id)


@tracer.start_as_current_span("Updating user's interacted_at")
async def touch_user_interaction(user_id: str) -> None:
    """Best-effort update of last user interaction without blocking request flow."""
    async with AsyncSessionLocal() as touch_db:
        try:
            await touch_db.execute(sa.text(f"SET LOCAL lock_timeout = '{INTERACTION_UPDATE_LOCK_TIMEOUT_MS}ms'"))
            await touch_db.execute(
                sa.update(User)
                .where(User.twitch_id == user_id)
                # .where(
                #     User.interacted_at
                #     < sa.func.now()
                #     - sa.text(f"INTERVAL '{INTERACTION_UPDATE_INTERVAL_SECONDS} seconds'")
                # )
                .values(interacted_at=sa.func.now())
            )
            await touch_db.commit()
        except DBAPIError:
            await touch_db.rollback()


@tracer.start_as_current_span("Twitch signature verification")
async def verify_eventsub_signature(
    request: Request,
    msg_id: str = Header(..., alias="Twitch-Eventsub-Message-Id"),
    msg_ts: str = Header(..., alias="Twitch-Eventsub-Message-Timestamp"),
    msg_type: str = Header(..., alias="Twitch-Eventsub-Message-Type"),
    msg_sig: str = Header(..., alias="Twitch-Eventsub-Message-Signature"),
) -> str:
    body = await request.body()
    hmac_msg = msg_id + msg_ts + body.decode("utf-8")
    expected = (
        "sha256="
        + hmac.new(
            key=settings.twitch_webhook_secret.get_secret_value().encode(),
            msg=hmac_msg.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()
    )
    if not hmac.compare_digest(expected, msg_sig):
        raise HTTPException(status_code=403, detail="Invalid signature")
    return msg_type


@tracer.start_as_current_span("User Authentification")
async def user_auth(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    user_id: str | None
    if not request.session or not (user_id := request.session.get("user_id")):
        raise HTTPException(status_code=401, detail="Not authorized")
    result = await db.execute(
        sa.select(User)
        .where(User.twitch_id == user_id)
        .options(
            joinedload(User.settings),
            joinedload(User.memealerts),
            joinedload(User.links),
            joinedload(User.tts),
            joinedload(User.telegram),
        )
    )
    user: User | None = result.scalar_one_or_none()  # type: ignore
    if not user:
        raise HTTPException(status_code=403, detail="User not found")

    await _ensure_not_banned(user)

    current_span = trace.get_current_span()
    if current_span.is_recording():
        current_span.set_attribute("auth.authorized", True)
        current_span.set_attribute("auth.user_id", user.id)
        current_span.set_attribute("auth.twitch_id", user.twitch_id)
        current_span.set_attribute("auth.twitch_name", user.login_name)

    asyncio.create_task(touch_user_interaction(user_id))

    # Фоновая перепроверка зафолловленности на создателя бота (не админ).
    # Throttle 30 мин — внутри refresh_followed_to_admin через Redis-ключ.
    if user.login_name != ADMIN_LOGIN:
        asyncio.create_task(refresh_followed_to_admin(user.id))

    # FIX:
    # sqlalchemy.exc.InterfaceError: (sqlalchemy.dialects.postgresql.asyncpg.InterfaceError) <class 'asyncpg.exceptions._base.InterfaceError'> cannot call Transaction.rollback(): the underlying connection is closed
    await db.commit()

    return user


@tracer.start_as_current_span("Staff Authentification")
async def staff_auth(user: Annotated[User, Security(user_auth)]) -> User:
    """Гейт штатного доступа (модератор/админ): страница «Сервер» и staff-API.

    Список — ``utils.staff.is_staff`` (источник ``REFERENCE_MODERATOR_LOGINS``,
    состав не меняем). Используется как Security-зависимость: неавторизованному
    — 401 (от user_auth), авторизованному не-штатному — 403 до вызова ручки.
    """
    if not is_staff(user):
        raise HTTPException(status_code=403, detail="No access to staff area")
    return user


@tracer.start_as_current_span("User Optional Authentification")
async def user_auth_optional(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    user_id: str | None
    if not request.session or not (user_id := request.session.get("user_id")):
        return None
    result = await db.execute(
        sa.select(User)
        .where(User.twitch_id == user_id)
        .options(
            joinedload(User.settings),
            joinedload(User.memealerts),
            joinedload(User.links),
            joinedload(User.tts),
            joinedload(User.telegram),
        )
    )
    user: User | None = result.scalar_one_or_none()  # type: ignore
    if user:
        await _ensure_not_banned(user)

        current_span = trace.get_current_span()
        if current_span.is_recording():
            current_span.set_attribute("auth.authorized", True)
            current_span.set_attribute("auth.user_id", user.id)
            current_span.set_attribute("auth.twitch_id", user.twitch_id)
            current_span.set_attribute("auth.twitch_name", user.login_name)

        asyncio.create_task(touch_user_interaction(user_id))

        # Фоновая перепроверка зафолловленности на создателя бота (не админ).
        # Throttle 30 мин — внутри refresh_followed_to_admin через Redis-ключ.
        if user.login_name != ADMIN_LOGIN:
            asyncio.create_task(refresh_followed_to_admin(user.id))
    else:
        current_span = trace.get_current_span()
        if current_span.is_recording():
            current_span.set_attribute("auth.authorized", False)

    # FIX:
    # sqlalchemy.exc.InterfaceError: (sqlalchemy.dialects.postgresql.asyncpg.InterfaceError) <class 'asyncpg.exceptions._base.InterfaceError'>: cannot call Transaction.rollback(): the underlying connection is closed
    await db.commit()

    return user


security = HTTPBasic()


@tracer.start_as_current_span("Admin Authentification")
def admin_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    current_username_bytes = credentials.username.encode("utf8")
    correct_username_bytes = settings.admin_api_login.encode()
    is_correct_username = secrets.compare_digest(current_username_bytes, correct_username_bytes)
    current_password_bytes = credentials.password.encode("utf8")
    correct_password_bytes = settings.admin_api_password.encode()
    is_correct_password = secrets.compare_digest(current_password_bytes, correct_password_bytes)
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
