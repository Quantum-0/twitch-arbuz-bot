from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from twitchAPI.type import AuthScope, TwitchResourceNotFound, UnauthorizedException

import twitch.client.twitch as twitch_module
from config import user_scope
from services.twitch_token_service import TwitchTokenExpiredError
from twitch.client.twitch import Twitch


def build_user(access_token: str | None = "token", refresh_token: str | None = "refresh") -> SimpleNamespace:
    return SimpleNamespace(
        access_token=access_token, refresh_token=refresh_token, login_name="streamer", twitch_id="123"
    )


def patch_token_service(
    monkeypatch: pytest.MonkeyPatch,
    get_valid_access_token: AsyncMock,
) -> None:
    container = SimpleNamespace(
        twitch_token_service=lambda: SimpleNamespace(get_valid_access_token=get_valid_access_token)
    )
    monkeypatch.setattr(twitch_module, "get_container", lambda: container)


def patch_twitch_client(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    client_mock = SimpleNamespace(set_user_authentication=AsyncMock())
    monkeypatch.setattr(twitch_module, "TwitchClient", AsyncMock(return_value=client_mock))
    return client_mock


async def test_no_tokens_raises_expired_before_service_call(monkeypatch: pytest.MonkeyPatch) -> None:
    constructor = AsyncMock()
    monkeypatch.setattr(twitch_module, "TwitchClient", constructor)
    get_valid = AsyncMock(return_value="fresh-token")
    patch_token_service(monkeypatch, get_valid)

    with pytest.raises(TwitchTokenExpiredError):
        await twitch_module._user_twitch_client(build_user(access_token=None))

    with pytest.raises(TwitchTokenExpiredError):
        await twitch_module._user_twitch_client(build_user(refresh_token=None))

    get_valid.assert_not_awaited()
    constructor.assert_not_called()


async def test_token_service_expired_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    get_valid = AsyncMock(side_effect=TwitchTokenExpiredError("no_access_token", "У пользователя нет access token"))
    patch_token_service(monkeypatch, get_valid)
    twitch_client = patch_twitch_client(monkeypatch)

    with pytest.raises(TwitchTokenExpiredError):
        await twitch_module._user_twitch_client(build_user())

    get_valid.assert_awaited_once()
    twitch_client.set_user_authentication.assert_not_awaited()


async def test_client_created_with_validated_token(monkeypatch: pytest.MonkeyPatch) -> None:
    get_valid = AsyncMock(return_value="fresh-token")
    patch_token_service(monkeypatch, get_valid)
    client_mock = patch_twitch_client(monkeypatch)

    result = await twitch_module._user_twitch_client(build_user())

    get_valid.assert_awaited_once()
    client_mock.set_user_authentication.assert_awaited_once_with("fresh-token", user_scope, "refresh", validate=False)
    assert result is client_mock  # noqa: S101 — проверка возвращаемого клиента


async def test_custom_scope_passed_to_twitch_client(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = [AuthScope.CHANNEL_MANAGE_REDEMPTIONS]
    patch_token_service(monkeypatch, AsyncMock(return_value="fresh-token"))
    client_mock = patch_twitch_client(monkeypatch)

    await twitch_module._user_twitch_client(build_user(), scope)

    client_mock.set_user_authentication.assert_awaited_once_with("fresh-token", scope, "refresh", validate=False)


async def test_validate_reward_subscription_treats_404_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client_mock = SimpleNamespace(get_custom_reward=AsyncMock(side_effect=TwitchResourceNotFound("404")))
    monkeypatch.setattr(twitch_module, "_user_twitch_client", AsyncMock(return_value=client_mock))

    problems = await Twitch.validate_reward_subscription(user=build_user(), reward_id="abc")  # type: ignore[arg-type]

    assert problems == ["Награда не найдена"]


async def test_reauth_decorator_converts_auth_errors() -> None:
    @twitch_module._reauth_on_auth_error
    async def failing() -> None:
        raise UnauthorizedException("401")

    with pytest.raises(TwitchTokenExpiredError):
        await failing()


async def test_reauth_decorator_passes_other_errors_through() -> None:
    @twitch_module._reauth_on_auth_error
    async def failing() -> None:
        raise TwitchResourceNotFound("404")

    with pytest.raises(TwitchResourceNotFound):
        await failing()


async def test_delete_reward_converts_unauthorized_to_reauth(monkeypatch: pytest.MonkeyPatch) -> None:
    client_mock = SimpleNamespace(delete_custom_reward=AsyncMock(side_effect=UnauthorizedException("401")))
    monkeypatch.setattr(twitch_module, "_user_twitch_client", AsyncMock(return_value=client_mock))

    with pytest.raises(TwitchTokenExpiredError):
        await Twitch.delete_reward(build_user(), "reward-id")
