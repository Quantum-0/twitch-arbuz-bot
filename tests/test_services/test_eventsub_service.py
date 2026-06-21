import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import insert

from database.models import User, TwitchUserSettings
from schemas.twitch import RaidWebhookSchema


@pytest.mark.asyncio
async def test_handle_raid_success(container, db_session, mock_twitch):
    # Setup test data
    user_id = 123
    # We need to use connection to bypass some logic if necessary, or just session
    user = User(
        twitch_id=str(user_id),
        login_name="testuser",
        profile_image_url="http://image",
        _access_token="enc_token",
        _refresh_token="enc_refresh"
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.commit()
    await db_session.refresh(user)

    # Settings are created by event listener in models.py after_insert
    # But since we use SQLite and maybe listeners don't work the same in all async setups,
    # let's ensure settings exist.
    # We should delete existing settings if they were created by listener to avoid Multiple rows error
    from sqlalchemy import delete
    await db_session.execute(delete(TwitchUserSettings).where(TwitchUserSettings.user_id == user.id))

    settings = TwitchUserSettings(user_id=user.id, enable_shoutout_on_raid=True)
    db_session.add(settings)
    await db_session.commit()

    service = container.twitch_eventsub_service()
    # Override db_session_factory in service to use our test session
    service._db_session_factory = MagicMock(return_value=db_session)

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
            "to_broadcaster_user_id": str(user_id),  # Use string to match schema and expectations
            "to_broadcaster_user_name": "testuser",
            "to_broadcaster_user_login": "testuser",
            "viewers": 10
        }
    }

    raid_payload = RaidWebhookSchema.model_validate(payload)
    print(f"Payload target user: {raid_payload.event.to_broadcaster_user_id}")
    print(f"DB User twitch_id: {user.twitch_id}")

    # We need to wait for the task created by @task_wrapper
    # Actually handle_raid is decorated with @task_wrapper which uses asyncio.create_task
    # To test it properly we might want to bypass @task_wrapper or wait for tasks.
    # For now, let's call the original function if possible or mock create_task.

    # The task_wrapper uses asyncio.create_task, so we need to wait a bit or use the original function
    # But since @staticmethod is used, let's see how to access it.
    # It seems I used it as a regular method in the test.
    # TwitchEventSubService.handle_raid is the decorated one.
    await service.handle_raid.__wrapped__(service, raid_payload)

    import asyncio
    await asyncio.sleep(0.1) # Give some time if there are any other tasks

    mock_twitch.shoutout.assert_called_once()
    # Check that shoutout was called with the correct user and raider id
    call_args = mock_twitch.shoutout.call_args
    assert call_args[1]["shoutout_to"] == 456
    assert call_args[1]["user"].twitch_id == str(user_id)

@pytest.mark.asyncio
async def test_handle_raid_disabled_shoutout(container, db_session, mock_twitch):
    user_id = 789
    user = User(
        twitch_id=str(user_id),
        login_name="no_shoutout",
        profile_image_url="http://image",
        _access_token="enc_token",
        _refresh_token="enc_refresh"
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.commit()
    await db_session.refresh(user)

    from sqlalchemy import delete
    await db_session.execute(delete(TwitchUserSettings).where(TwitchUserSettings.user_id == user.id))

    settings = TwitchUserSettings(user_id=user.id, enable_shoutout_on_raid=False)
    db_session.add(settings)
    await db_session.commit()

    service = container.twitch_eventsub_service()
    service._db_session_factory = MagicMock(return_value=db_session)

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
            "to_broadcaster_user_name": "no_shoutout",
            "to_broadcaster_user_login": "no_shoutout",
            "viewers": 10
        }
    }

    raid_payload = RaidWebhookSchema.model_validate(payload)
    await service.handle_raid.__wrapped__(service, raid_payload)

    mock_twitch.shoutout.assert_not_called()
    mock_twitch.unsubscribe_raid.assert_called_once()
