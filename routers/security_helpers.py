import hashlib
import hmac

import sqlalchemy as sa
from fastapi import Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.requests import Request

from config import settings
from database.models import User
from dependencies import get_db


async def verify_eventsub_signature(
    request: Request,
    msg_id: str = Header(..., alias="Twitch-Eventsub-Message-Id"),
    msg_ts: str = Header(..., alias="Twitch-Eventsub-Message-Timestamp"),
    msg_type: str = Header(..., alias="Twitch-Eventsub-Message-Type"),
    msg_sig: str = Header(..., alias="Twitch-Eventsub-Message-Signature"),
):
    body = await request.body()
    hmac_msg = msg_id + msg_ts + body.decode('utf-8')
    expected = "sha256=" + hmac.new(
        key=settings.twitch_webhook_secret.get_secret_value().encode(),
        msg=hmac_msg.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, msg_sig):
        raise HTTPException(status_code=403, detail="Invalid signature")
    return msg_type


async def user_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    if not request.session or not (user_id := request.session.get("user_id")):
        raise HTTPException(status_code=401, detail="Not authorized")
    result = await db.execute(
        sa.select(User)
        .options(
            selectinload(User.settings),
            selectinload(User.memealerts),
        )
        .filter_by(twitch_id=user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=403, detail="User not found")
    return user
