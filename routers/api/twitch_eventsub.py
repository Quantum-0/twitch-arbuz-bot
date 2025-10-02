import asyncio
import logging.config
from collections import deque
from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Path
from memealerts.types.exceptions import MATokenExpiredError
from sqlalchemy.orm import selectinload
from starlette.responses import PlainTextResponse, Response
from twitchAPI.type import TwitchResourceNotFound

from database.database import AsyncSessionLocal
from database.models import User, TwitchUserSettings
from dependencies import get_twitch, get_chat_bot
from routers.helpers import parse_eventsub_payload
from routers.schemas import (
    PointRewardRedemptionWebhookSchema,
    TwitchChallengeSchema,
    RaidWebhookSchema,
    ChatMessageSchema,
)
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
    payload: Annotated[
        PointRewardRedemptionWebhookSchema
        | RaidWebhookSchema
        | TwitchChallengeSchema
        | ChatMessageSchema,
        Depends(parse_eventsub_payload),
    ],
    twitch: Annotated[Twitch, Depends(get_twitch)],
    chat_bot: Annotated[ChatBot, Depends(get_chat_bot)],
    streamer_id: int = Path(...),
):
    logger.info(f"Got eventsub. Type: {type(payload)}")

    # Ответ на challenge сразу
    if isinstance(payload, TwitchChallengeSchema):
        return PlainTextResponse(content=payload.challenge, media_type="text/plain")

    # Мгновенно возвращаем 204, а обработку делаем в фоне
    if isinstance(payload, PointRewardRedemptionWebhookSchema):
        # Отброс дубликатов
        if payload.event.redemption_id in local_duplicates_cache:
            logger.warning(
                f"Duplicated eventsub with redemption={payload.event.redemption_id}"
            )
            return Response(status_code=204)
        logger.info("Handling reward redemption")
        asyncio.create_task(
            handle_reward_redemption(payload, streamer_id, twitch, chat_bot)
        )
    elif isinstance(payload, RaidWebhookSchema):
        logger.info("Handling raid")
        asyncio.create_task(handle_raid(payload, twitch))
    elif isinstance(payload, ChatMessageSchema):
        logger.info("Handling message webhook")
        asyncio.create_task(chat_bot.on_message(payload.event))
    return Response(status_code=204)


async def handle_raid(payload: RaidWebhookSchema, twitch: Twitch):
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                sa.select(User)
                .options(selectinload(User.settings))
                .filter_by(login_name=payload.event.to_broadcaster_user_login)
            )
            user = result.scalar_one_or_none()
            if user is None:
                logger.error(
                    f"User not found for login: {payload.event.to_broadcaster_user_login}"
                )
                return
            user_settings: TwitchUserSettings = user.settings

            if not user_settings.enable_shoutout_on_raid:
                await twitch.unsubscribe_raid(
                    subscription_id=payload.subscription.subscription_id
                )
                return

            await twitch.shoutout(
                user=user, shoutout_to=payload.event.from_broadcaster_user_id
            )
        except Exception:
            logger.error("Error handling raid event", exc_info=True)


async def handle_reward_redemption(
    payload: PointRewardRedemptionWebhookSchema,
    streamer_id: int,
    twitch: Twitch,
    chat_bot: ChatBot,
):
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                sa.select(User)
                .options(selectinload(User.memealerts))
                .filter_by(login_name=payload.event.broadcaster_user_login)
            )
            user = result.scalar_one_or_none()
            if user is None:
                logger.error(
                    f"User not found for login: {payload.event.broadcaster_user_login}"
                )
                return

            # Логика обработки вознаграждения
            result = await give_bonus(
                user.memealerts.memealerts_token,
                user.login_name,
                supporter=payload.event.user_input,
                amount=user.memealerts.coins_for_reward,
            )

            if result:
                await chat_bot.send_message(user, "Мемкоины начислены :з")
                try:
                    await twitch.fulfill_redemption(
                        user,
                        payload.subscription.condition.reward_id,
                        payload.event.redemption_id,
                    )
                except TwitchResourceNotFound:
                    logger.error("Cannot find redemption to fulfill", exc_info=True)
            else:
                await chat_bot.send_message(
                    user,
                    "Ошибка начисления >.< Баллы возвращены 👀. Проверьте имя пользователя на мемалёрте!",
                )
                try:
                    await twitch.cancel_redemption(
                        user,
                        payload.subscription.condition.reward_id,
                        payload.event.redemption_id,
                    )
                except TwitchResourceNotFound:
                    logger.error("Cannot find redemption to cancel", exc_info=True)

        except MATokenExpiredError:
            logger.error("MA Token expired")
            await chat_bot.send_message(
                user,
                f"Ошибка начисления мемкоинов. @{user.login_name}, истёк срок действия токена. Пожалуйста, обновите токен в панели управления ботом.",
            )
            await twitch.cancel_redemption(
                user,
                payload.subscription.condition.reward_id,
                payload.event.redemption_id,
            )
        except Exception:
            logger.error("Error handling redemption", exc_info=True)
            try:
                await chat_bot.send_message(
                    user,
                    "Непредвиденная ошибка начисления мемкоинов! О.О Баллы возвращены!",
                )
                await twitch.cancel_redemption(
                    user,
                    payload.subscription.condition.reward_id,
                    payload.event.redemption_id,
                )
            except Exception:
                logger.exception(
                    "Failed to send error message or cancel redemption", exc_info=True
                )

        finally:
            # Кешируем, чтобы не дублировать
            local_duplicates_cache.append(payload.event.redemption_id)
