import hashlib
import hmac
import secrets
from typing import Annotated

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import Depends, Header, HTTPException
from sqlalchemy.exc import DBAPIError
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status
from starlette.requests import Request

from config import settings
from database.database import AsyncSessionLocal
from database.models import User
from dependencies import get_db

INTERACTION_UPDATE_INTERVAL_SECONDS = 15
INTERACTION_UPDATE_LOCK_TIMEOUT_MS = 50


async def touch_user_interaction(user_id: str) -> None:
    """Best-effort update of last user interaction without blocking request flow."""
    async with AsyncSessionLocal() as touch_db:
        try:
            await touch_db.execute(
                sa.text(f"SET LOCAL lock_timeout = '{INTERACTION_UPDATE_LOCK_TIMEOUT_MS}ms'")
            )
            await touch_db.execute(
                sa.update(User)
                .where(User.twitch_id == user_id)
                .where(
                    User.interacted_at
                    < sa.func.now()
                    - sa.text(f"INTERVAL '{INTERACTION_UPDATE_INTERVAL_SECONDS} seconds'")
                )
                .values(interacted_at=sa.func.now())
            )
            await touch_db.commit()
        except DBAPIError:
            await touch_db.rollback()


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


@inject
async def user_auth(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if not request.session or not (user_id := request.session.get("user_id")):
        raise HTTPException(status_code=401, detail="Not authorized")
    result = await db.execute(
        sa.select(User)
        .where(User.twitch_id == user_id)
        .options(
            selectinload(User.settings),
            selectinload(User.memealerts),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=403, detail="User not found")
    await touch_user_interaction(user_id)
    return user


@inject
async def user_auth_optional(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    if not request.session or not (user_id := request.session.get("user_id")):
        return None
    result = await db.execute(
        sa.select(User)
        .where(User.twitch_id == user_id)
        .options(
            selectinload(User.settings),
            selectinload(User.memealerts),
        )
    )
    user = result.scalar_one_or_none()
    if user:
        await touch_user_interaction(user_id)
    return user


security = HTTPBasic()


def admin_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    current_username_bytes = credentials.username.encode("utf8")
    correct_username_bytes = settings.admin_api_login.encode()
    is_correct_username = secrets.compare_digest(
        current_username_bytes, correct_username_bytes
    )
    current_password_bytes = credentials.password.encode("utf8")
    correct_password_bytes = settings.admin_api_password.encode()
    is_correct_password = secrets.compare_digest(
        current_password_bytes, correct_password_bytes
    )
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
