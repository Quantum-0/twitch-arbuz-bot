import asyncio
import logging.config
from uuid import UUID

from fastapi import APIRouter, Security, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import PlainTextResponse, Response
import sqlalchemy as sa
from twitchAPI.types import TwitchResourceNotFound

from database.models import User
from dependencies import get_db, get_twitch, get_chat_bot
from routers.schemas import PointRewardRedemptionWebhookSchema, TwitchChallengeSchema
from routers.security_helpers import verify_eventsub_signature
from twitch.bot import ChatBot
from twitch.twitch import Twitch
from utils.logging_conf import LOGGING_CONFIG

from utils.memes import give_bonus

router = APIRouter(prefix="/twitch")
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)

local_duplicates_cache: list[UUID] = []

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

    # Skip duplicates
    if payload.event.redemption_id in local_duplicates_cache:
        logger.warning(f"Duplicated eventsub with redemption={payload.event.redemption_id}")
        return Response(status_code=204)

    # Notification: channel points reward redemption
    logging.info(f"Notification from twitch: {payload}")
    result = await db.execute(
        sa.select(User)
        .options(selectinload(User.memealerts))
        .filter_by(login_name=payload.event.broadcaster_user_login)
    )
    user = result.scalar_one_or_none()

    # Small sleep, maybe that will fix twitch's twitchAPI.types.TwitchResourceNotFound
    # await asyncio.sleep(3)

    if eventsub_message_type == "notification":
        try:
            result = await give_bonus(user.memealerts.memealerts_token, user.login_name, supporter=payload.event.user_input, amount=user.memealerts.coins_for_reward)
        except Exception as exc:
            logger.error(exc, exc_info=True)
            await chat_bot.send_message(user, "Непредвиденная ошибка начисления мемкоинов! О.О Баллы возвращены!")
            await twitch.cancel_redemption(user, payload.subscription.condition.reward_id, payload.event.redemption_id)
            pass
        if result:
            await chat_bot.send_message(user, "Мемкоины начислены :з")
            try:
                await twitch.fulfill_redemption(user, payload.subscription.condition.reward_id, payload.event.redemption_id)
            except TwitchResourceNotFound:
                logger.error("Cannot find redemption")
                pass
        else:
            await chat_bot.send_message(user, "Ошибка начисления >.< Баллы возвращены 👀. Проверьте имя пользователя на мемалёрте!")
            try:
                await twitch.cancel_redemption(user, payload.subscription.condition.reward_id, payload.event.redemption_id)
            except TwitchResourceNotFound:
                logger.error("Cannot find redemption")
                pass

    local_duplicates_cache.append(payload.event.redemption_id)
    if len(local_duplicates_cache) > 30:
        del local_duplicates_cache[0]
    return Response(status_code=204)