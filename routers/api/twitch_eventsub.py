import asyncio
import logging.config
from collections import deque
from uuid import UUID

from fastapi import APIRouter, Security, Depends, Path, HTTPException, Header
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
import sqlalchemy as sa
from twitchAPI.types import TwitchResourceNotFound

from database.database import AsyncSessionLocal
from database.models import User, TwitchUserSettings
from dependencies import get_twitch, get_chat_bot
from routers.schemas import PointRewardRedemptionWebhookSchema, TwitchChallengeSchema, RaidWebhookSchema
from routers.security_helpers import verify_eventsub_signature
from twitch.bot import ChatBot
from twitch.twitch import Twitch
from utils.logging_conf import LOGGING_CONFIG

from utils.memes import give_bonus

router = APIRouter(prefix="/twitch")
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)

local_duplicates_cache: deque[UUID] = deque(maxlen=50)

SCHEMA_BY_TYPE: dict[str, type[BaseModel]] = {
    "channel.raid": RaidWebhookSchema,
    "channel.channel_points_custom_reward_redemption.add": PointRewardRedemptionWebhookSchema,
    "webhook_callback_verification": TwitchChallengeSchema,
}

@router.post("/eventsub/{streamer_id}")
async def eventsub_handler(
    # payload: PointRewardRedemptionWebhookSchema | TwitchChallengeSchema | RaidWebhookSchema,
    request: Request,
    eventsub_message_type: str = Security(verify_eventsub_signature),
    eventsub_subscription_type: str = Header(..., alias="Twitch-Eventsub-Subscription-Type"),
    streamer_id: int = Path(...),
    twitch: Twitch = Depends(get_twitch),
    chat_bot: ChatBot = Depends(get_chat_bot),
):
    body = await request.json()
    schema_cls = SCHEMA_BY_TYPE.get(eventsub_subscription_type)
    if schema_cls is None:
        logger.warning(f"Couldn't determine schema by eventsub_subscription_type: {eventsub_subscription_type}")
        # если тип неожиданный — можно попытаться угадать по содержимому.
        last_err = None
        for candidate in SCHEMA_BY_TYPE.values():
            try:
                payload = candidate.model_validate(body)
                break
            except ValidationError as e:
                last_err = e
        else:
            logger.error(f"Couldn't validate: {body}")
            raise HTTPException(
                status_code=400,
                detail=f"Unknown eventsub_subscription_type '{eventsub_subscription_type}' "
                       f"and body did not match any expected schema. "
                       f"Last validation error: {last_err.errors() if last_err else 'none'}",
            )
    else:
        logger.info(f"Determine schema {schema_cls} by eventsub_subscription_type: {eventsub_subscription_type}")
        try:
            payload = schema_cls.model_validate(body)
        except ValidationError as e:
            # явное падение по ожидаемой схеме — покажем ошибку валидации
            raise HTTPException(status_code=422, detail=e.errors())

    # Ответ на challenge сразу
    if isinstance(payload, TwitchChallengeSchema):
        return PlainTextResponse(content=payload.challenge, media_type="text/plain")

    # Отброс дубликатов
    if payload.event.redemption_id in local_duplicates_cache:
        logger.warning(f"Duplicated eventsub with redemption={payload.event.redemption_id}")
        return Response(status_code=204)

    logger.debug("All checks are done, handling eventsub")

    # Мгновенно возвращаем 204, а обработку делаем в фоне
    if isinstance(payload, PointRewardRedemptionWebhookSchema):
        logger.info("Handling reward redemption")
        asyncio.create_task(handle_reward_redemption(payload, streamer_id, twitch, chat_bot))
    elif isinstance(payload, RaidWebhookSchema):
        logger.info("Handling raid")
        asyncio.create_task(handle_raid(payload, twitch))
    return Response(status_code=204)


async def handle_raid(payload: RaidWebhookSchema, twitch: Twitch):
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                sa.select(User)
                .options(selectinload(User.settings))
                .filter_by(login_name=payload.event.broadcaster_user_login)
            )
            user = result.scalar_one_or_none()
            if user is None:
                logger.error(f"User not found for login: {payload.event.broadcaster_user_login}")
                return
            user_settings: TwitchUserSettings = user.settings

            if not user_settings.enable_shoutout_on_raid:
                await twitch.unsubscribe_raid(payload.subscription.subscription_id)
                return

            await twitch.shoutout(user=user, shoutout_to=payload.event.from_broadcaster_user_id)
        except Exception:
            logger.error("Error handling raid event", exc_info=True)


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
