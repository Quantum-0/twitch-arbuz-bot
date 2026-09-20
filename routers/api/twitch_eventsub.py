import asyncio
import logging.config
from collections import deque
from typing import Annotated
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Path
from starlette.responses import PlainTextResponse, Response

from config import settings
from container import Container
from routers.helpers import parse_eventsub_payload
from schemas.api import BaseErrorSchema
from schemas.twitch import (
    ChatMessageSchema,
    FollowWebhookSchema,
    PointRewardRedemptionWebhookSchema,
    RaidWebhookSchema,
    StreamOfflineSchema,
    StreamOnlineSchema,
    SubscribeWebhookSchema,
    SubscriptionMessageWebhookSchema,
    TwitchChallengeSchema,
)
from services.eventsub_service import TwitchEventSubService
from services.mqtt import MQTTClient
from twitch.chat.bot import ChatBot

router = APIRouter(prefix="/twitch")

logger = logging.getLogger(__name__)

local_duplicates_cache: deque[UUID] = deque(maxlen=50)


@router.post(
    "/eventsub/{streamer_id}",
    status_code=204,
    responses={
        200: {
            "description": "Challenge response with text/plain",
            "content": {"text/plain": {"example": "p7q9384u5y9834u5"}},
        },
        204: {"description": "Successful handling message"},
        400: {
            "description": "Invalid JSON body or Unknown EventSub message type",
            "model": BaseErrorSchema,
        },
        403: {
            "description": "Invalid signature",
            "model": BaseErrorSchema,
        },
    },
)
@inject
async def eventsub_handler(
    payload: Annotated[
        PointRewardRedemptionWebhookSchema
        | RaidWebhookSchema
        | TwitchChallengeSchema
        | ChatMessageSchema
        | StreamOnlineSchema
        | StreamOfflineSchema
        | FollowWebhookSchema
        | SubscribeWebhookSchema
        | SubscriptionMessageWebhookSchema,
        Depends(parse_eventsub_payload),
    ],
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    mqtt: Annotated[MQTTClient, Depends(Provide[Container.mqtt])],
    service: Annotated[TwitchEventSubService, Depends(Provide[Container.twitch_eventsub_service])],
    streamer_id: Annotated[int, Path(...)],
):
    logger.info(f"Got eventsub. Type: {type(payload)}")

    if isinstance(payload, TwitchChallengeSchema):
        return PlainTextResponse(content=payload.challenge, media_type="text/plain")

    if isinstance(payload, PointRewardRedemptionWebhookSchema):
        logger.info("Handling reward redemption")
        await mqtt.publish(f"twitch/{payload.subscription.condition.broadcaster_user_id}/reward-redemption", payload)
        if settings.direct_handle_rewards:
            await service.handle_reward_redemption(payload)
    elif isinstance(payload, RaidWebhookSchema):
        logger.info("Handling raid")
        await service.handle_raid(payload)
        await mqtt.publish(f"twitch/{payload.subscription.condition.to_broadcaster_user_id}/raid", payload)
    elif isinstance(payload, ChatMessageSchema):
        logger.info("Handling message webhook")
        if settings.direct_handle_messages:
            asyncio.create_task(chat_bot.on_message(payload.event))
        await mqtt.publish(f"twitch/{payload.subscription.condition.broadcaster_user_id}/message", payload.event)
    elif isinstance(payload, StreamOnlineSchema):
        logger.info("Handling stream.online")
        await service.handle_stream_online(payload)
    elif isinstance(payload, StreamOfflineSchema):
        logger.info("Handling stream.offline")
        await service.handle_stream_offline(payload)
    elif isinstance(payload, FollowWebhookSchema):
        logger.info("Handling follow")
        await service.handle_follow(payload)
    elif isinstance(payload, SubscribeWebhookSchema):
        logger.info("Handling subscribe")
        await service.handle_subscribe(payload)
    elif isinstance(payload, SubscriptionMessageWebhookSchema):
        logger.info("Handling subscription message")
        await service.handle_subscription_message(payload)
    return Response(status_code=204)
