import logging

from fastapi import APIRouter, Security, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import PlainTextResponse, Response
import sqlalchemy as sa

from database.models import User
from dependencies import get_db, get_twitch, get_chat_bot
from routers.schemas import PointRewardRedemptionWebhookSchema, TwitchChallengeSchema
from routers.security_helpers import verify_eventsub_signature
from twitch.bot import ChatBot
from twitch.twitch import Twitch

from utils.memes import give_bonus

router = APIRouter(prefix="/twitch")
logger = logging.getLogger()

@router.post("/eventsub/{streamer_id}")
async def eventsub_handler(
    payload: PointRewardRedemptionWebhookSchema | TwitchChallengeSchema,
    eventsub_message_type: bytes = Security(verify_eventsub_signature),
    streamer_id: int = Path(...),
    db: AsyncSession = Depends(get_db),
    twitch: Twitch = Depends(get_twitch),
    chat_bot: ChatBot = Depends(get_chat_bot),
):
    # Challenge
    if eventsub_message_type == "webhook_callback_verification":
        return PlainTextResponse(content=payload.challenge, media_type="text/plain")

    # Notification: channel points reward redemption
    logging.info(f"Notification from twitch: {payload}")
    result = await db.execute(
        sa.select(User)
        .options(selectinload(User.memealerts))
        .filter_by(login_name=payload.event.broadcaster_user_login)
    )
    user = result.scalar_one_or_none()

    if eventsub_message_type == "notification":
        try:
            result = await give_bonus(user.memealerts.memealerts_token, user.login_name, payload.event.user_login, amount=2)
        except Exception as exc:
            logger.error(exc)
            await chat_bot._chat.send_message(user.login_name, "Непредвиденная ошибка начисления мемкоинов! О.О Баллы возвращены!")
            # await twitch.cancel_redemption(user, )
            raise
        if result:
            pass
            # await twitch.fulfill_redemption()
        else:
            pass
            # await twitch.cancel_redemption()

    return Response(status_code=204)