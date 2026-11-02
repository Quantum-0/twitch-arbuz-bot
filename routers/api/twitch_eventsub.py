import logging.config
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
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
    EventSubRevocationSchema,
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
from services.stream_notifications import StreamNotificationsService
from twitch.chat.bot import ChatBot
from utils.observability import spawn_logged_task

router = APIRouter(prefix="/twitch")

logger = logging.getLogger(__name__)

local_duplicates_cache: deque[UUID] = deque(maxlen=50)


async def _handle_revocation(payload: EventSubRevocationSchema, service: TwitchEventSubService) -> None:
    logger.info("Handling eventsub revocation")
    await service.handle_revocation(payload)


async def _handle_follow(payload: FollowWebhookSchema, service: TwitchEventSubService) -> None:
    logger.info("Handling follow")
    await service.handle_follow(payload)


async def _handle_subscribe(payload: SubscribeWebhookSchema, service: TwitchEventSubService) -> None:
    logger.info("Handling subscribe")
    await service.handle_subscribe(payload)


async def _handle_subscription_message(
    payload: SubscriptionMessageWebhookSchema, service: TwitchEventSubService
) -> None:
    logger.info("Handling subscription message")
    await service.handle_subscription_message(payload)


_SERVICE_HANDLERS: dict[type, Callable[[Any, TwitchEventSubService], Awaitable[None]]] = {
    EventSubRevocationSchema: _handle_revocation,
    FollowWebhookSchema: _handle_follow,
    SubscribeWebhookSchema: _handle_subscribe,
    SubscriptionMessageWebhookSchema: _handle_subscription_message,
}


async def _handle_reward_redemption(
    payload: PointRewardRedemptionWebhookSchema, mqtt: MQTTClient, service: TwitchEventSubService
) -> None:
    logger.info("Handling reward redemption")
    await mqtt.publish(f"twitch/{payload.subscription.condition.broadcaster_user_id}/reward-redemption", payload)
    if settings.app.direct_handle_rewards:
        await service.handle_reward_redemption(payload)


async def _handle_raid(payload: RaidWebhookSchema, mqtt: MQTTClient, service: TwitchEventSubService) -> None:
    logger.info("Handling raid")
    await service.handle_raid(payload)
    await mqtt.publish(f"twitch/{payload.subscription.condition.to_broadcaster_user_id}/raid", payload)


async def _handle_chat_message(payload: ChatMessageSchema, chat_bot: ChatBot, mqtt: MQTTClient) -> None:
    logger.info("Handling message webhook")
    if settings.app.direct_handle_messages:
        spawn_logged_task(
            chat_bot.on_message(payload.event),
            name=f"chat_on_message:{payload.subscription.condition.broadcaster_user_id}",
        )
    await mqtt.publish(f"twitch/{payload.subscription.condition.broadcaster_user_id}/message", payload.event)


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
        | EventSubRevocationSchema
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
    stream_notifications: Annotated[StreamNotificationsService, Depends(Provide[Container.stream_notifications])],
    streamer_id: Annotated[int, Path(...)],  # noqa: ARG001
):
    logger.info(f"Got eventsub. Type: {type(payload)}")

    if isinstance(payload, TwitchChallengeSchema):
        return PlainTextResponse(content=payload.challenge, media_type="text/plain")
    if isinstance(payload, PointRewardRedemptionWebhookSchema):
        await _handle_reward_redemption(payload, mqtt, service)
    elif isinstance(payload, RaidWebhookSchema):
        await _handle_raid(payload, mqtt, service)
    elif isinstance(payload, ChatMessageSchema):
        await _handle_chat_message(payload, chat_bot, mqtt)
    elif isinstance(payload, StreamOnlineSchema):
        logger.info("Handling stream.online")
        await stream_notifications.handle_stream_online(payload)
    elif isinstance(payload, StreamOfflineSchema):
        logger.info("Handling stream.offline")
        await stream_notifications.handle_stream_offline(payload)
    elif (handler := _SERVICE_HANDLERS.get(type(payload))) is not None:
        await handler(payload, service)
    return Response(status_code=204)
