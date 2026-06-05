from typing import Annotated

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession

from container import Container
from dependencies import get_db
from database.models import User
from routers.security_helpers import admin_auth, user_auth
from twitch.chat.bot import ChatBot

router = APIRouter(prefix="/admin", tags=["Admin API"])


@router.post("/add_to_beta_test")
async def update_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Security(admin_auth)],
    __: Annotated[None, Security(user_auth)],
    twitch_login: Annotated[str, Query(...)],
):
    q = sa.update(User).values(in_beta_test=True).where(User.login_name == twitch_login)
    res = await db.execute(q)
    await db.commit()
    return {"success": res.rowcount}


@router.post("/send_message")
@inject
async def send_message(
    db: Annotated[AsyncSession, Depends(get_db)],
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    _: Annotated[None, Security(admin_auth)],
    channel: Annotated[str, Query(...)],
    message: Annotated[str, Query(...)],
):
    q = sa.select(User).where(User.login_name == channel)
    res: User | None = await db.scalar(q)
    if not res:
        raise HTTPException(status_code=404, detail="User not found")

    await chat_bot.send_message(res, message)
