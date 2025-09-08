from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Security, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from dependencies import get_db, get_twitch
from routers.security_helpers import admin_auth
from twitch.twitch import Twitch

router = APIRouter(prefix="/admin", tags=["Admin API"])

@router.post("/add_to_beta_test")
async def update_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = Security(admin_auth),
    twitch_login: str = Query(...),
):
    q = sa.update(User).values(in_beta_test=True).where(User.login_name == twitch_login)
    res = await db.execute(q)
    await db.commit()
    return {"success": res.rowcount}


@router.post("/send_message")
async def send_message(
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(get_twitch)],
    _: None = Security(admin_auth),
    channel: str = Query(...),
    message: str = Query(...),
):
    q = sa.select(User).where(User.login_name == channel)
    res: User | None = await db.scalar(q)
    if not res:
        raise HTTPException(status_code=404, detail="User not found")

    await twitch.send_chat_message(res, message)
    # return {"success": res.rowcount}