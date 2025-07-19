import logging
from typing import Any

from fastapi import APIRouter, Security, Body, Depends, Path
from sqlalchemy.orm import Session
from starlette.responses import PlainTextResponse, Response

from database.models import User
from dependencies import get_db
from routers.security_helpers import verify_eventsub_signature

from utils.memealerts import give_bonus

router = APIRouter(prefix="/twitch")

@router.post("/eventsub/{streamer_id:int}")
async def eventsub_handler(
    eventsub_message_type: bytes = Security(verify_eventsub_signature),
    streamer_id: int = Path(...),
    payload: Any = Body(...),
    db: Session = Depends(get_db),
):
    # Challenge
    if eventsub_message_type == "webhook_callback_verification":
        challenge = payload.get("challenge", "")
        return PlainTextResponse(content=challenge, media_type="text/plain")

    # Notification: channel points reward redemption
    # {'user': 'Quantum075', 'reward': 'test reward by bot', 'timestamp': '2025-07-19T01:36:47.520426697Z', 'input': 'test'}
    user = db.query(User).filter_by(login_name=payload["user"]).first()

    if eventsub_message_type == "notification":
        give_bonus(user.memealerts.memealerts_token, user.login_name, payload["input"])

    return Response(status_code=204)