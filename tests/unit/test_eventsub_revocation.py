from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from schemas.twitch import EventSubRevocationSchema
from services.eventsub_service import TwitchEventSubService

# Реальный payload из прода (GlitchTip): Twitch ретраил его из-за 422,
# потому что схемы ждали поле event.
REVOCATION_CHAT_PAYLOAD = {
    "subscription": {
        "id": "50016cc8-2f18-4fcf-9a6f-3db31eb3c4e7",
        "status": "authorization_revoked",
        "type": "channel.chat.message",
        "version": "1",
        "condition": {"broadcaster_user_id": "654179372", "user_id": "957818216"},
        "transport": {
            "method": "webhook",
            "callback": "https://bot.quantum0.ru/api/twitch/eventsub/654179372",
        },
        "created_at": "2026-08-18T15:06:10.916031757Z",
        "cost": 0,
    }
}


def build_service(cache=None) -> TwitchEventSubService:
    service = object.__new__(TwitchEventSubService)
    service._cache = cache
    service._db_session_factory = None
    return service


def build_revocation(sub_type: str, condition: dict) -> EventSubRevocationSchema:
    return EventSubRevocationSchema.model_validate(
        {
            "subscription": {
                "id": "50016cc8-2f18-4fcf-9a6f-3db31eb3c4e7",
                "status": "authorization_revoked",
                "type": sub_type,
                "version": "1",
                "condition": condition,
                "transport": {
                    "method": "webhook",
                    "callback": "https://bot.quantum0.ru/api/twitch/eventsub/1",
                },
                "created_at": "2026-08-18T15:06:10.916031757Z",
                "cost": 0,
            }
        }
    )


def test_revocation_schema_parses_real_payload() -> None:
    payload = EventSubRevocationSchema.model_validate(REVOCATION_CHAT_PAYLOAD)

    sub = payload.subscription
    assert sub.type == "channel.chat.message"
    assert sub.status == "authorization_revoked"
    assert sub.condition.broadcaster_user_id == 654179372
    assert sub.condition.user_id == 957818216


def test_revocation_schema_raid_condition() -> None:
    payload = build_revocation("channel.raid", {"to_broadcaster_user_id": "111", "from_broadcaster_user_id": "222"})

    assert payload.subscription.condition.to_broadcaster_user_id == 111
    assert payload.subscription.condition.broadcaster_user_id is None


@pytest.mark.asyncio
async def test_chat_message_revocation_disables_chat_bot() -> None:
    service = build_service()
    service._disable_chat_bot = AsyncMock()

    await service.handle_revocation(EventSubRevocationSchema.model_validate(REVOCATION_CHAT_PAYLOAD))

    service._disable_chat_bot.assert_awaited_once_with(654179372)


@pytest.mark.asyncio
async def test_reward_revocation_touches_nothing() -> None:
    cache = SimpleNamespace(delete=AsyncMock())
    service = build_service(cache=cache)

    await service.handle_revocation(
        build_revocation(
            "channel.channel_points_custom_reward_redemption.add",
            {"broadcaster_user_id": "654179372", "reward_id": "aaaabbbb-cccc-dddd-eeee-ffff00001111"},
        )
    )

    cache.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_follow_revocation_clears_overlay_redis_key() -> None:
    cache = SimpleNamespace(delete=AsyncMock())
    service = build_service(cache=cache)

    await service.handle_revocation(build_revocation("channel.follow", {"broadcaster_user_id": "654179372"}))

    cache.delete.assert_awaited_once_with("eventsub:overlay:654179372")


@pytest.mark.asyncio
async def test_revocation_without_broadcaster_is_ignored() -> None:
    cache = SimpleNamespace(delete=AsyncMock())
    service = build_service(cache=cache)

    await service.handle_revocation(build_revocation("channel.follow", {}))

    cache.delete.assert_not_awaited()
