import logging

from fastapi import Request, HTTPException, Security, Header
from pydantic import ValidationError, BaseModel

from routers.schemas import (
    RaidWebhookSchema,
    TwitchChallengeSchema,
    PointRewardRedemptionWebhookSchema,
    ChatMessageSchema,
)
from routers.security_helpers import verify_eventsub_signature

logger = logging.getLogger(__name__)

# 👇 маппинг типов события -> схема
SCHEMA_BY_TYPE: dict[str, type[BaseModel]] = {
    "channel.raid": RaidWebhookSchema,
    "channel.channel_points_custom_reward_redemption.add": PointRewardRedemptionWebhookSchema,
    "webhook_callback_verification": TwitchChallengeSchema,
    "channel.chat.message": ChatMessageSchema,
}


async def parse_eventsub_payload(
    request: Request,
    eventsub_subscription_type: str = Header(
        ..., alias="Twitch-Eventsub-Subscription-Type"
    ),
    eventsub_message_type: str = Security(verify_eventsub_signature),
) -> PointRewardRedemptionWebhookSchema | RaidWebhookSchema | TwitchChallengeSchema:
    """
    Reusable dependency that parses the incoming EventSub payload and returns the appropriate schema.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    schema_cls: BaseModel | None = (
        TwitchChallengeSchema
        if eventsub_message_type == "webhook_callback_verification"
        else SCHEMA_BY_TYPE.get(eventsub_subscription_type)
    )

    if not schema_cls:
        logger.warning(
            f"Determine schema {schema_cls} by eventsub_subscription_type: {eventsub_subscription_type}"
        )
        logger.debug(body)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown EventSub message type: {eventsub_subscription_type}",
        )

    try:
        return schema_cls(**body)
    except ValidationError as exc:
        logger.error(f"Failed to validate: {exc.errors()}")
        logger.error(body, exc_info=True)
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
