import hashlib
import hmac
import json
import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete

from database.models import User, TwitchUserSettings


def get_twitch_signature(msg_id, msg_ts, body, secret):
    hmac_msg = msg_id + msg_ts + body
    return "sha256=" + hmac.new(
        key=secret.encode(),
        msg=hmac_msg.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

@pytest.mark.asyncio
async def test_eventsub_handler_challenge(client):
    from config import settings
    challenge = "test_challenge_123"
    payload = {
        "challenge": challenge,
        "subscription": {
            "id": str(uuid.uuid4()),
            "status": "enabled",
            "type": "webhook_callback_verification",
            "version": "1",
            "condition": {},
            "transport": {
                "method": "webhook",
                "callback": "http://localhost"
            },
            "created_at": datetime.now().isoformat()
        }
    }

    # We need to mock parse_eventsub_payload dependency or provide headers that make it work
    # Looking at routers/helpers.py might be useful.
    # For now, let's assume we can mock the dependency if we want, but let's try a direct call first.

    msg_id = str(uuid.uuid4())
    msg_ts = datetime.now().isoformat()
    body = json.dumps(payload, separators=(',', ':'))
    signature = get_twitch_signature(msg_id, msg_ts, body, settings.twitch_webhook_secret.get_secret_value())

    response = client.post(
        "/api/twitch/eventsub/123",
        content=body,
        headers={
            "Twitch-Eventsub-Message-Type": "webhook_callback_verification",
            "Twitch-Eventsub-Message-Id": msg_id,
            "Twitch-Eventsub-Message-Timestamp": msg_ts,
            "Twitch-Eventsub-Message-Signature": signature,
            "Twitch-Eventsub-Subscription-Type": "webhook_callback_verification",
            "Content-Type": "application/json"
        }
    )

    # Actually parse_eventsub_payload validates the signature.
    # We should probably override the dependency in the app for testing.

    assert response.status_code == 200
    assert response.text == challenge

@pytest.mark.asyncio
async def test_eventsub_handler_raid(client, container, db_session):
    from config import settings as app_settings
    user_id = 999
    # Create user in DB
    user = User(
        twitch_id=str(user_id),
        login_name="routertestuser",
        profile_image_url="http://image",
        _access_token="enc_token",
        _refresh_token="enc_refresh"
    )
    db_session.add(user)
    await db_session.commit()

    await db_session.execute(delete(TwitchUserSettings).where(TwitchUserSettings.user_id == user.id))
    u_settings = TwitchUserSettings(user_id=user.id, enable_shoutout_on_raid=True)
    db_session.add(u_settings)
    await db_session.commit()

    # Mock service

    with container.twitch_eventsub_service.override(MagicMock()) as service_mock:
        service = service_mock()
        service.handle_raid = AsyncMock()

        payload = {
            "subscription": {
                "id": str(uuid.uuid4()),
                "status": "enabled",
                "type": "channel.raid",
                "version": 1,
                "cost": 1,
                "condition": {
                    "from_broadcaster_user_id": "456",
                    "to_broadcaster_user_id": str(user_id)
                },
                "transport": {
                    "method": "webhook",
                    "callback": "http://localhost/callback"
                },
                "created_at": datetime.now().isoformat()
            },
            "event": {
                "from_broadcaster_user_id": 456,
                "from_broadcaster_user_name": "raider",
                "from_broadcaster_user_login": "raider",
                "to_broadcaster_user_id": user_id,
                "to_broadcaster_user_name": "testuser",
                "to_broadcaster_user_login": "testuser",
                "viewers": 10
            }
        }

        msg_id = str(uuid.uuid4())
        msg_ts = datetime.now().isoformat()
        body = json.dumps(payload, separators=(',', ':'))
        signature = get_twitch_signature(msg_id, msg_ts, body, app_settings.twitch_webhook_secret.get_secret_value())

        response = client.post(
            f"/api/twitch/eventsub/{user_id}",
            content=body,
            headers={
                "Twitch-Eventsub-Message-Type": "notification",
                "Twitch-Eventsub-Message-Id": msg_id,
                "Twitch-Eventsub-Message-Timestamp": msg_ts,
                "Twitch-Eventsub-Message-Signature": signature,
                "Twitch-Eventsub-Subscription-Type": "channel.raid",
                "Content-Type": "application/json"
            }
        )

        assert response.status_code == 204
        service.handle_raid.assert_called_once()
