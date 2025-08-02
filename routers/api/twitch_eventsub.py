import asyncio
import logging.config
from collections import deque
from uuid import UUID

from fastapi import APIRouter, Security, Depends, Path
from sqlalchemy.orm import selectinload
from starlette.responses import PlainTextResponse, Response
import sqlalchemy as sa
from twitchAPI.types import TwitchResourceNotFound

from database.database import AsyncSessionLocal
from database.models import User
from dependencies import get_twitch, get_chat_bot
from routers.schemas import PointRewardRedemptionWebhookSchema, TwitchChallengeSchema
from routers.security_helpers import verify_eventsub_signature
from twitch.bot import ChatBot
from twitch.twitch import Twitch
from utils.logging_conf import LOGGING_CONFIG

from utils.memes import give_bonus

router = APIRouter(prefix="/twitch")
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)

local_duplicates_cache: deque[UUID] = deque(maxlen=50)

@router.post("/eventsub/{streamer_id}")
async def eventsub_handler(
    payload: PointRewardRedemptionWebhookSchema | TwitchChallengeSchema,
    eventsub_message_type: str = Security(verify_eventsub_signature),
    streamer_id: int = Path(...),
    twitch: Twitch = Depends(get_twitch),
    chat_bot: ChatBot = Depends(get_chat_bot),
):
    # Ответ на challenge сразу
    if eventsub_message_type == "webhook_callback_verification":
        return PlainTextResponse(content=payload.challenge, media_type="text/plain")

    # Отброс дубликатов
    if payload.event.redemption_id in local_duplicates_cache:
        logger.warning(f"Duplicated eventsub with redemption={payload.event.redemption_id}")
        return Response(status_code=204)

    # Мгновенно возвращаем 204, а обработку делаем в фоне
    asyncio.create_task(handle_reward_redemption(payload, streamer_id, twitch, chat_bot))
    return Response(status_code=204)


async def handle_reward_redemption(payload: PointRewardRedemptionWebhookSchema, streamer_id: int, twitch: Twitch, chat_bot: ChatBot):
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                sa.select(User)
                .options(selectinload(User.memealerts))
                .filter_by(login_name=payload.event.broadcaster_user_login)
            )
            user = result.scalar_one_or_none()
            if user is None:
                logger.error(f"User not found for login: {payload.event.broadcaster_user_login}")
                return

            # Логика обработки вознаграждения
            result = await give_bonus(
                user.memealerts.memealerts_token,
                user.login_name,
                supporter=payload.event.user_input,
                amount=user.memealerts.coins_for_reward
            )

            if result:
                await chat_bot.send_message(user, "Мемкоины начислены :з")
                try:
                    await twitch.fulfill_redemption(user, payload.subscription.condition.reward_id, payload.event.redemption_id)
                except TwitchResourceNotFound:
                    logger.error("Cannot find redemption to fulfill", exc_info=True)
            else:
                await chat_bot.send_message(user, "Ошибка начисления >.< Баллы возвращены 👀. Проверьте имя пользователя на мемалёрте!")
                try:
                    await twitch.cancel_redemption(user, payload.subscription.condition.reward_id, payload.event.redemption_id)
                except TwitchResourceNotFound:
                    logger.error("Cannot find redemption to cancel", exc_info=True)

        except Exception:
            logger.error("Error handling redemption", exc_info=True)
            try:
                await chat_bot.send_message(user, "Непредвиденная ошибка начисления мемкоинов! О.О Баллы возвращены!")
                await twitch.cancel_redemption(user, payload.subscription.condition.reward_id, payload.event.redemption_id)
            except Exception:
                logger.exception("Failed to send error message or cancel redemption", exc_info=True)

        finally:
            # Кешируем, чтобы не дублировать
            local_duplicates_cache.append(payload.event.redemption_id)
