import asyncio
import logging.config
from collections import deque
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from starlette.responses import PlainTextResponse, Response

from config import settings
from dependencies import get_chat_bot, get_mqtt, get_twitch_eventsub_service
from routers.helpers import parse_eventsub_payload
from routers.schemas import (
    ChatMessageSchema,
    PointRewardRedemptionWebhookSchema,
    RaidWebhookSchema,
    TwitchChallengeSchema,
)
from services.eventsub_service import TwitchEventSubService
from services.mqtt import MQTTClient
from twitch.chat.bot import ChatBot

router = APIRouter(prefix="/twitch")

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
    chat_bot: Annotated[ChatBot, Depends(get_chat_bot)],
    mqtt: Annotated[MQTTClient, Depends(get_mqtt)],
    service: Annotated[TwitchEventSubService, Depends(get_twitch_eventsub_service)],
    streamer_id: int = Path(...),
):
    logger.info(f"Got eventsub. Type: {type(payload)}")

    # Ответ на challenge сразу
    if isinstance(payload, TwitchChallengeSchema):
        return PlainTextResponse(content=payload.challenge, media_type="text/plain")

    # Мгновенно возвращаем 204, а обработку делаем в фоне
    if isinstance(payload, PointRewardRedemptionWebhookSchema):
        logger.info("Handling reward redemption")
        await mqtt.publish(f"twitch/{payload.subscription.condition.broadcaster_user_id}/reward-redemption", payload.event)
        if settings.direct_handle_rewards:
            await service.handle_reward_redemption(payload)
    elif isinstance(payload, RaidWebhookSchema):
        logger.info("Handling raid")
        await service.handle_raid(payload)
        await mqtt.publish(f"twitch/{payload.subscription.condition.broadcaster_user_id}/raid", payload.event)
    elif isinstance(payload, ChatMessageSchema):
        logger.info("Handling message webhook")
        if settings.direct_handle_messages:
            asyncio.create_task(chat_bot.on_message(payload.event))
        await mqtt.publish(f"twitch/{payload.subscription.condition.broadcaster_user_id}/message", payload.event)
    return Response(status_code=204)
