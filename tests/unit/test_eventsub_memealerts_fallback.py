from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from memealerts.types.exceptions import MAUserNotFoundError

from exceptions import MAInvalidTokenError
from services.eventsub_service import TwitchEventSubService


def build_service() -> TwitchEventSubService:
    service = object.__new__(TwitchEventSubService)
    service._statistics = None
    service._memealerts_auth = SimpleNamespace(get_token_of_user=AsyncMock(return_value="v2-token"))
    service._memealerts_v2 = SimpleNamespace(give_bonus=AsyncMock())
    service._memealerts = SimpleNamespace(give_bonus=AsyncMock())
    service._chatbot = SimpleNamespace(send_message=AsyncMock())
    service._twitch = SimpleNamespace(cancel_redemption=AsyncMock(), fulfill_redemption=AsyncMock())
    return service


def build_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=10,
        twitch_id="123",
        login_name="streamer",
        memealerts=SimpleNamespace(
            access_token="oauth-token",
            memealerts_token="legacy-token",
            coins_for_reward=2,
            memecoin_name_accusative=None,
            memecoin_name_genitive=None,
            memecoin_name_genitive_multiple=None,
        ),
    )


def build_payload() -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(user_input="viewer-id", user_name="viewer", redemption_id="redemption"),
        subscription=SimpleNamespace(condition=SimpleNamespace(reward_id="reward")),
    )


@pytest.mark.asyncio
async def test_v2_business_error_does_not_fallback_to_v1() -> None:
    service = build_service()
    service._memealerts_v2.give_bonus.side_effect = MAUserNotFoundError

    await service.reward_buy_memealerts(user=build_user(), payload=build_payload())

    service._memealerts.give_bonus.assert_not_awaited()
    service._twitch.cancel_redemption.assert_awaited_once()
    message = service._chatbot.send_message.await_args.args[1]
    assert "не найден" in message
    assert "токен" not in message.lower()


@pytest.mark.asyncio
async def test_v2_token_error_falls_back_to_v1() -> None:
    service = build_service()
    service._memealerts_v2.give_bonus.side_effect = MAInvalidTokenError
    service._memealerts.give_bonus.return_value = True

    await service.reward_buy_memealerts(user=build_user(), payload=build_payload())

    service._memealerts.give_bonus.assert_awaited_once()
    service._twitch.fulfill_redemption.assert_awaited_once()
    service._twitch.cancel_redemption.assert_not_awaited()
