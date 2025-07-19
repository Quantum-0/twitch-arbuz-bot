import logging
from typing import Any

from fastapi import APIRouter, Security, Body
from starlette.responses import PlainTextResponse, Response

from routers.security_helpers import verify_eventsub_signature

router = APIRouter(prefix="/twitch")

@router.post("/eventsub")
async def eventsub_handler(
    eventsub_message_type: bytes = Security(verify_eventsub_signature),
    payload: Any = Body(...)
):
    # Challenge
    if eventsub_message_type == "webhook_callback_verification":
        challenge = payload.get("challenge", "")
        return PlainTextResponse(content=challenge, media_type="text/plain")

    # Notification: channel points reward redemption
    if (
        eventsub_message_type == "notification"
        and payload["subscription"]["type"]
        == "channel.channel_points_custom_reward_redemption.add"
    ):
        event = payload["event"]
        data = {
            "user": event.get("user_name"),
            "reward": event.get("reward", {}).get("title"),
            "timestamp": event.get("redeemed_at"),
            "input": event.get("user_input"),
        }
        print(data)
        logger = logging.getLogger()
        logger.error(data)

    return Response(status_code=204)