from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from twitchAPI.type import InvalidRefreshTokenException, TwitchResourceNotFound

import twitch.client.twitch as twitch_module
from services.twitch_token_service import TwitchTokenExpiredError
from twitch.client.twitch import Twitch


def build_user(access_token: str | None = "token", refresh_token: str | None = "refresh") -> SimpleNamespace:
    return SimpleNamespace(
        access_token=access_token, refresh_token=refresh_token, login_name="streamer", twitch_id="123"
    )


async def test_no_tokens_raises_expired() -> None:
    with pytest.raises(TwitchTokenExpiredError):
        await twitch_module._user_twitch_client(build_user(access_token=None))

    with pytest.raises(TwitchTokenExpiredError):
        await twitch_module._user_twitch_client(build_user(refresh_token=None))


async def test_invalid_refresh_token_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    client_mock = SimpleNamespace(
        set_user_authentication=AsyncMock(side_effect=InvalidRefreshTokenException("Invalid refresh token"))
    )
    monkeypatch.setattr(twitch_module, "TwitchClient", AsyncMock(return_value=client_mock))

    with pytest.raises(TwitchTokenExpiredError):
        await twitch_module._user_twitch_client(build_user())


async def test_validate_reward_subscription_treats_404_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client_mock = SimpleNamespace(get_custom_reward=AsyncMock(side_effect=TwitchResourceNotFound("404")))
    monkeypatch.setattr(twitch_module, "_user_twitch_client", AsyncMock(return_value=client_mock))

    problems = await Twitch.validate_reward_subscription(user=build_user(), reward_id="abc")  # type: ignore[arg-type]

    assert problems == ["Награда не найдена"]
