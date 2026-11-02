# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты Twitch-клиента: app-token, отправка сообщений, EventSub-подписки,
награды (update/validate/fix_autofulfill) и модерация."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from twitchAPI.type import TwitchResourceNotFound, UnauthorizedException

import twitch.client.twitch as tw
from tests.unit.fixtures.fake_redis import FakeRedis
from twitch.client.twitch import Twitch


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = str(body)

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._text = value

    @property
    def is_success(self) -> bool:
        return self.status_code < 400

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class HttpRouterFake:
    """Фейк httpx.AsyncClient: маршрутизация POST по URL."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.eventsub_response = FakeResponse(200, {"id": "sub-1"})
        self.chat_response = FakeResponse(200, {"message_id": "m1"})
        self.ext_response = FakeResponse(204)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"url": url, **kwargs})
        if "oauth2/token" in url:
            return FakeResponse(200, {"access_token": "app-token", "expires_in": 3600})
        if "eventsub/subscriptions" in url:
            return self.eventsub_response
        if "chat/messages" in url:
            return self.chat_response
        if "extensions/chat" in url:
            return self.ext_response
        return FakeResponse(200)

    # для совместимости
    async def get(self, url: str, **kwargs: Any) -> FakeResponse:  # noqa: ARG002
        return FakeResponse(200)


def make_sub(sub_id: str, sub_type: str, condition: dict[str, str], status: str = "enabled") -> SimpleNamespace:
    return SimpleNamespace(id=sub_id, type=sub_type, condition=condition, status=status)


def async_iter(items: list[Any]):
    class _Iter:
        def __init__(self) -> None:
            self._items = list(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._items:
                return self._items.pop(0)
            raise StopAsyncIteration

    return _Iter()


@pytest.fixture
def http(monkeypatch) -> HttpRouterFake:
    router = HttpRouterFake()
    monkeypatch.setattr(tw.httpx, "AsyncClient", lambda *a, **kw: router)  # noqa: ARG005
    return router


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def client(redis) -> Twitch:
    # Twitch — @singleton: сбрасываем кэш, чтобы тесты были изолированы
    Twitch._instance = None
    return Twitch(redis)


def user_stub(twitch_id: str = "123456", login: str = "chan") -> SimpleNamespace:
    return SimpleNamespace(twitch_id=twitch_id, login_name=login)


class TestAppAccessToken:
    async def test_cache_hit_no_http(self, client, redis, http):
        redis.values[tw._APP_TOKEN_REDIS_KEY] = "cached-token"
        assert await client._get_app_access_token() == "cached-token"
        assert http.requests == []

    async def test_fetch_and_cache(self, client, redis, http):
        token = await client._get_app_access_token()
        assert token == "app-token"
        assert redis.values[tw._APP_TOKEN_REDIS_KEY] == "app-token"
        assert redis.ttls[tw._APP_TOKEN_REDIS_KEY] == tw._APP_TOKEN_TTL
        token_req = http.requests[0]
        assert token_req["params"]["grant_type"] == "client_credentials"


class TestSendMessages:
    async def test_raw_ok(self, client, http):
        client._twitch = SimpleNamespace(get_app_token=lambda: "bot-token")
        result = await client.send_chat_message_raw("111", "222", "hi", reply_parent_message_id="pm-1")
        assert result == {"message_id": "m1"}
        req = http.requests[0]
        assert req["json"] == {
            "broadcaster_id": "111",
            "sender_id": "222",
            "message": "hi",
            "reply_parent_message_id": "pm-1",
        }

    async def test_raw_error_raises(self, client, http):
        client._twitch = SimpleNamespace(get_app_token=lambda: "bot-token")
        http.chat_response = FakeResponse(500, {"error": "boom"})
        with pytest.raises(Exception, match="Twitch error"):
            await client.send_chat_message_raw("111", "222", "hi")

    async def test_send_chat_message_fallback(self, client, http):
        client._twitch = SimpleNamespace(
            get_app_token=lambda: "bot-token",
            send_chat_message=AsyncMock(return_value="sent-via-sdk"),
        )
        http.chat_response = FakeResponse(500, {"error": "down"})

        result = await client.send_chat_message("111", "chan", "hi", reply_parent_message_id="pm", for_source_only=True)

        assert result == "sent-via-sdk"
        client._twitch.send_chat_message.assert_awaited_once_with(
            broadcaster_id="111",
            sender_id="957818216",
            message="hi",
            reply_parent_message_id="pm",
            for_source_only=True,
        )

    async def test_extension_message_truncated(self, client, http, monkeypatch):
        monkeypatch.setattr(tw.settings.twitch, "extension_id", "ext-1")
        monkeypatch.setattr(tw.settings.twitch, "extension_version", "2.0")
        long_text = "x" * 500

        await client.send_extension_chat_message("111", long_text)

        req = http.requests[0]
        assert req["url"] == "https://api.twitch.tv/helix/extensions/chat"
        assert req["params"] == {"broadcaster_id": "111"}
        assert len(req["json"]["text"]) == 280
        assert req["json"]["extension_id"] == "ext-1"
        assert req["headers"]["Client-Id"] == "ext-1"
        assert req["headers"]["Authorization"].startswith("Bearer ")


class TestStartup:
    async def test_startup_sets_client(self, client, monkeypatch):
        fake_tc = SimpleNamespace(set_user_authentication=AsyncMock())
        monkeypatch.setattr(tw, "TwitchClient", AsyncMock(return_value=fake_tc))

        await client.startup()

        assert client._twitch is fake_tc
        fake_tc.set_user_authentication.assert_awaited_once()


class TestGetSubscriptions:
    async def test_happy_path(self, client):
        subs = [make_sub("1", "stream.online", {"broadcaster_user_id": "5"})]
        client._twitch = SimpleNamespace(get_eventsub_subscriptions=AsyncMock(return_value=async_iter(subs)))
        assert await client.get_subscriptions() == subs

    async def test_unauthorized_reauth_retry(self, client, monkeypatch):
        calls = {"n": 0}

        async def get_subs():
            calls["n"] += 1
            if calls["n"] == 1:
                raise UnauthorizedException("expired")
            return async_iter([make_sub("2", "stream.offline", {})])

        fake_tc = SimpleNamespace(
            get_eventsub_subscriptions=get_subs,
            set_user_authentication=AsyncMock(),
        )

        async def factory(*args: Any, **kwargs: Any) -> SimpleNamespace:  # noqa: ARG002
            return fake_tc

        monkeypatch.setattr(tw, "TwitchClient", factory)
        client._twitch = fake_tc

        result = await client.get_subscriptions()

        assert [s.id for s in result] == ["2"]
        assert fake_tc.set_user_authentication.await_count == 1


class TestSubscribeChatMessages:
    async def test_yields_per_user(self, client, http, monkeypatch):
        monkeypatch.setattr(tw.settings.twitch, "webhook_secret", __import__("pydantic").SecretStr("whsec"))
        monkeypatch.setattr(
            tw.settings.twitch, "reward_redemption_webhook", "https://cb.example.com/twitch/reward-redemption"
        )
        user1 = user_stub("111", "one")
        user2 = user_stub("222", "two")
        responses = [FakeResponse(200), FakeResponse(403, {"message": "no auth"})]

        async def post(url: str, **kwargs: Any) -> FakeResponse:
            http.requests.append({"url": url, **kwargs})
            if "oauth2/token" in url:
                return FakeResponse(200, {"access_token": "app-token"})
            return responses.pop(0)

        http.post = post  # type: ignore[method-assign]

        results = [item async for item in client.subscribe_chat_messages(user1, user2)]

        assert results[0] == (user1, True, {})
        assert results[1] == (user2, False, {"message": "no auth"})
        sub_req = http.requests[1]
        assert sub_req["json"]["type"] == "channel.chat.message"
        assert sub_req["json"]["condition"] == {"broadcaster_user_id": "111", "user_id": "957818216"}
        assert sub_req["json"]["transport"]["callback"] == "https://cb.example.com/twitch/reward-redemption/111"


class TestEventSubSubscribes:
    @pytest.fixture
    def cached_token(self, redis):
        redis.values[tw._APP_TOKEN_REDIS_KEY] = "cached"
        return redis

    @pytest.mark.parametrize(
        ("method_name", "sub_type", "condition_key"),
        [
            ("subscribe_follow", "channel.follow", "broadcaster_user_id"),
            ("subscribe_subscribe", "channel.subscribe", "broadcaster_user_id"),
            ("subscribe_subscription_message", "channel.subscription.message", "broadcaster_user_id"),
            ("subscribe_stream_online", "stream.online", "broadcaster_user_id"),
            ("subscribe_stream_offline", "stream.offline", "broadcaster_user_id"),
            ("subscribe_raid", "channel.raid", "to_broadcaster_user_id"),
        ],
    )
    async def test_subscribe_payload(
        self, client, http, cached_token, monkeypatch, method_name, sub_type, condition_key
    ):
        monkeypatch.setattr(tw.settings.twitch, "webhook_secret", __import__("pydantic").SecretStr("whsec"))
        monkeypatch.setattr(
            tw.settings.twitch, "reward_redemption_webhook", "https://cb.example.com/twitch/reward-redemption"
        )
        method = getattr(client, method_name)
        await method(user_stub("777"))
        req = http.requests[0]
        assert req["json"]["type"] == sub_type
        assert req["json"]["condition"][condition_key] == "777"
        assert req["headers"]["Authorization"].startswith("Bearer ")

    async def test_subscribe_http_error_raises(self, client, http, redis):
        redis.values[tw._APP_TOKEN_REDIS_KEY] = "cached"
        http.eventsub_response = FakeResponse(409, {"error": "conflict"})
        with pytest.raises(RuntimeError, match="409"):
            await client.subscribe_stream_online(user_stub("1"))

    async def test_subscribe_reward_payload(self, client, http):
        reward_id = uuid4()
        result = await Twitch.subscribe_reward(user_stub("111"), reward_id)
        assert result == {"id": "sub-1"}
        sub_req = http.requests[1]
        assert sub_req["json"]["type"] == "channel.channel_points_custom_reward_redemption.add"
        assert sub_req["json"]["condition"] == {"broadcaster_user_id": "111", "reward_id": str(reward_id)}


class TestUnsubscribes:
    async def test_unsubscribe_by_type_removes_duplicates(self, client):
        subs = [
            make_sub("s1", "stream.online", {"broadcaster_user_id": "111"}),
            make_sub("s2", "stream.online", {"broadcaster_user_id": "111"}),  # дубль
            make_sub("s3", "stream.online", {"broadcaster_user_id": "222"}),
            make_sub("s4", "stream.offline", {"broadcaster_user_id": "111"}),
        ]
        delete = AsyncMock()
        client._twitch = SimpleNamespace(
            get_eventsub_subscriptions=AsyncMock(return_value=async_iter(subs)),
            delete_eventsub_subscription=delete,
        )

        assert await client.unsubscribe_by_type(user_stub("111"), "stream.online") is True
        assert [c.kwargs.get("subscription_id") or c.args[0] for c in delete.await_args_list] == ["s1", "s2"]

    async def test_unsubscribe_by_type_raid_condition(self, client):
        subs = [make_sub("s1", "channel.raid", {"to_broadcaster_user_id": "111"})]
        delete = AsyncMock()
        client._twitch = SimpleNamespace(
            get_eventsub_subscriptions=AsyncMock(return_value=async_iter(subs)),
            delete_eventsub_subscription=delete,
        )
        assert await client.unsubscribe_by_type(user_stub("111"), "channel.raid") is True

    async def test_unsubscribe_by_type_nothing(self, client):
        client._twitch = SimpleNamespace(
            get_eventsub_subscriptions=AsyncMock(return_value=async_iter([])),
            delete_eventsub_subscription=AsyncMock(),
        )
        assert await client.unsubscribe_by_type(user_stub("111"), "stream.online") is False

    @pytest.mark.parametrize(
        ("method", "sub_type"),
        [
            ("unsubscribe_stream_online", "stream.online"),
            ("unsubscribe_stream_offline", "stream.offline"),
        ],
    )
    async def test_stream_unsubscribe(self, client, method, sub_type):
        subs = [make_sub("s1", sub_type, {"broadcaster_user_id": "111"})]
        delete = AsyncMock()
        client._twitch = SimpleNamespace(
            get_eventsub_subscriptions=AsyncMock(return_value=async_iter(subs)),
            delete_eventsub_subscription=delete,
        )
        assert await getattr(client, method)(user_stub("111")) is True
        delete.assert_awaited_once_with(subscription_id="s1")

    async def test_unsubscribe_raid_by_user(self, client):
        subs = [make_sub("s1", "channel.raid", {"to_broadcaster_user_id": "111"})]
        delete = AsyncMock()
        client._twitch = SimpleNamespace(
            get_eventsub_subscriptions=AsyncMock(return_value=async_iter(subs)),
            delete_eventsub_subscription=delete,
        )
        assert await client.unsubscribe_raid(user=user_stub("111")) is True

    async def test_unsubscribe_raid_by_id(self, client):
        delete = AsyncMock()
        client._twitch = SimpleNamespace(delete_eventsub_subscription=delete)
        assert await client.unsubscribe_raid(subscription_id="sub-x") is True
        delete.assert_awaited_once_with(subscription_id="sub-x")

    async def test_unsubscribe_raid_requires_exactly_one_arg(self, client):
        with pytest.raises(ValueError):
            await client.unsubscribe_raid()
        with pytest.raises(ValueError):
            await client.unsubscribe_raid(user=user_stub("1"), subscription_id="s")


class FakeUserClient:
    def __init__(self, rewards=None, subs=None, raise_on_rewards=None):
        self._rewards = rewards or []
        self._subs = subs or []
        self._raise = raise_on_rewards
        self.get_custom_reward = AsyncMock(side_effect=self._get_rewards)
        self.update_custom_reward = AsyncMock()
        self.delete_custom_reward = AsyncMock()
        self.get_eventsub_subscriptions = AsyncMock(return_value=SimpleNamespace(data=self._subs))
        self.delete_eventsub_subscription = AsyncMock()

    async def _get_rewards(self, *args: Any, **kwargs: Any):
        if self._raise:
            raise self._raise
        return self._rewards


def reward_stub(enabled=True, input_required=True, skip_queue=False) -> SimpleNamespace:
    return SimpleNamespace(
        is_enabled=enabled,
        is_user_input_required=input_required,
        should_redemptions_skip_request_queue=skip_queue,
    )


@pytest.fixture
def patch_user_client(monkeypatch):
    def _patch(fake: FakeUserClient):
        async def factory(user, scope=None):  # noqa: ARG001
            return fake

        monkeypatch.setattr(tw, "_user_twitch_client", factory)
        return fake

    return _patch


class TestValidateRewardSubscription:
    async def test_reward_not_found_404(self, patch_user_client):
        patch_user_client(FakeUserClient(raise_on_rewards=TwitchResourceNotFound()))
        assert await Twitch.validate_reward_subscription(user_stub(), "r1") == ["Награда не найдена"]

    async def test_reward_empty_list(self, patch_user_client):
        patch_user_client(FakeUserClient(rewards=[]))
        assert await Twitch.validate_reward_subscription(user_stub(), "r1") == ["Награда не найдена"]

    async def test_reward_flags_problems(self, patch_user_client):
        patch_user_client(FakeUserClient(rewards=[reward_stub(enabled=False, input_required=False, skip_queue=True)]))
        problems = await Twitch.validate_reward_subscription(user_stub(), "r1")
        assert "Награда отключена" in problems
        assert "Ввод имени пользователя отключён" in problems
        assert "Включён пропуск очереди на награды" in problems

    async def test_enabled_subscription_ok(self, patch_user_client):
        subs = [
            make_sub(
                "s1",
                "channel.channel_points_custom_reward_redemption.add",
                {"broadcaster_user_id": "123456", "reward_id": "r1"},
                status="enabled",
            )
        ]
        patch_user_client(FakeUserClient(rewards=[reward_stub()], subs=subs))
        assert await Twitch.validate_reward_subscription(user_stub(), "r1") == []

    async def test_disabled_subscription(self, patch_user_client):
        subs = [
            make_sub(
                "s1",
                "channel.channel_points_custom_reward_redemption.add",
                {"broadcaster_user_id": "123456", "reward_id": "r1"},
                status="disabled",
            )
        ]
        patch_user_client(FakeUserClient(rewards=[reward_stub()], subs=subs))
        assert await Twitch.validate_reward_subscription(user_stub(), "r1") == ["Подписка на награду не активна"]

    async def test_no_subscription(self, patch_user_client):
        subs = [
            make_sub(
                "s2",
                "channel.channel_points_custom_reward_redemption.add",
                {"broadcaster_user_id": "123456", "reward_id": "other"},
            )
        ]
        patch_user_client(FakeUserClient(rewards=[reward_stub()], subs=subs))
        assert await Twitch.validate_reward_subscription(user_stub(), "r1") == ["Подписка на награду не найдена"]


class TestUpdateReward:
    async def test_reward_missing_raises(self, patch_user_client):
        patch_user_client(FakeUserClient(rewards=[]))
        with pytest.raises(TwitchResourceNotFound):
            await Twitch.update_reward(
                user_stub(),
                uuid4(),
                is_enabled=True,
                is_user_input_required=True,
                should_redemptions_skip_request_queue=False,
            )

    async def test_disabled_sub_resubscribed(self, patch_user_client, monkeypatch):
        reward_id = uuid4()
        subs = [
            make_sub(
                "s1",
                "channel.channel_points_custom_reward_redemption.add",
                {"broadcaster_user_id": "123456", "reward_id": str(reward_id)},
                status="disabled",
            )
        ]
        fake = patch_user_client(FakeUserClient(rewards=[reward_stub()], subs=subs))
        called = {}

        async def fake_subscribe(user, reward_id):  # noqa: ARG001
            called["ok"] = True

        monkeypatch.setattr(tw.Twitch, "subscribe_reward", staticmethod(fake_subscribe))

        await Twitch.update_reward(
            user_stub(),
            reward_id,
            is_enabled=True,
            is_user_input_required=True,
            should_redemptions_skip_request_queue=False,
        )

        fake.delete_eventsub_subscription.assert_awaited_once_with("s1")
        assert called.get("ok") is True

    async def test_enabled_sub_kept(self, patch_user_client, monkeypatch):
        reward_id = uuid4()
        subs = [
            make_sub(
                "s1",
                "channel.channel_points_custom_reward_redemption.add",
                {"broadcaster_user_id": "123456", "reward_id": str(reward_id)},
                status="enabled",
            )
        ]
        fake = patch_user_client(FakeUserClient(rewards=[reward_stub()], subs=subs))
        monkeypatch.setattr(tw.Twitch, "subscribe_reward", staticmethod(AsyncMock()))

        await Twitch.update_reward(
            user_stub(),
            reward_id,
            is_enabled=True,
            is_user_input_required=True,
            should_redemptions_skip_request_queue=False,
        )

        fake.delete_eventsub_subscription.assert_not_awaited()


class TestFixRewardAutofulfill:
    async def test_not_found_false(self, patch_user_client):
        patch_user_client(FakeUserClient(raise_on_rewards=TwitchResourceNotFound()))
        assert await Twitch.fix_reward_autofulfill(user_stub(), "r1") is False

    async def test_already_disabled_false(self, patch_user_client):
        fake = patch_user_client(FakeUserClient(rewards=[reward_stub(skip_queue=False)]))
        assert await Twitch.fix_reward_autofulfill(user_stub(), "r1") is False
        fake.update_custom_reward.assert_not_awaited()

    async def test_enabled_gets_disabled(self, patch_user_client):
        fake = patch_user_client(FakeUserClient(rewards=[reward_stub(skip_queue=True)]))
        assert await Twitch.fix_reward_autofulfill(user_stub(), "r1") is True
        fake.update_custom_reward.assert_awaited_once_with("123456", "r1", should_redemptions_skip_request_queue=False)


class TestModerationAndHelpers:
    async def test_shoutout(self, client):
        client._twitch = SimpleNamespace(send_a_shoutout=AsyncMock())
        await client.shoutout(user_stub("111"), 222)
        client._twitch.send_a_shoutout.assert_awaited_once_with(
            from_broadcaster_id="111", to_broadcaster_id="222", moderator_id="957818216"
        )

    async def test_send_warning(self, client):
        client._twitch = SimpleNamespace(warn_chat_user=AsyncMock())
        await client.send_warning(user_stub("111"), "999", reason="spam")
        client._twitch.warn_chat_user.assert_awaited_once_with(
            broadcaster_id="111", moderator_id="957818216", user_id="999", reason="spam"
        )

    async def test_delete_message(self, client):
        client._twitch = SimpleNamespace(delete_chat_message=AsyncMock())
        await client.delete_message(user_stub("111"), "msg-1")
        client._twitch.delete_chat_message.assert_awaited_once_with(
            broadcaster_id="111", moderator_id="957818216", message_id="msg-1"
        )

    async def test_delete_reward(self, patch_user_client):
        fake = patch_user_client(FakeUserClient())
        await Twitch.delete_reward(user_stub("111"), "r1")
        fake.delete_custom_reward.assert_awaited_once_with("111", "r1")

    async def test_disable_reward(self, patch_user_client):
        fake = patch_user_client(FakeUserClient())
        await Twitch.disable_reward(user_stub("111"), "r1")
        fake.update_custom_reward.assert_awaited_once_with("111", "r1", is_enabled=False)

    async def test_get_followers_count(self, client):
        client._twitch = SimpleNamespace(get_channel_followers=AsyncMock(return_value=SimpleNamespace(total=42)))
        assert await client.get_followers_count(user_stub("111")) == 42

    async def test_check_user_follows_admin(self, client):
        client._twitch = SimpleNamespace(get_channel_followers=AsyncMock(return_value=SimpleNamespace(data=[object()])))
        assert await client.check_user_follows_admin("1", "2") is True

    async def test_get_bot_user_by_login(self, client):
        async def get_users(logins):
            assert logins == ["target"]
            yield SimpleNamespace(login="target", id="42")

        client._twitch = SimpleNamespace(get_users=get_users)
        result = await client.get_bot_user_by_login("  @Target ")
        assert result.id == "42"

    async def test_get_bot_user_by_login_empty(self, client):
        client._twitch = SimpleNamespace(get_users=AsyncMock())
        assert await client.get_bot_user_by_login("   ") is None

    async def test_get_bot_user_by_login_not_found(self, client):
        async def no_users(logins):  # noqa: ARG001
            return
            yield  # pragma: no cover

        client._twitch = SimpleNamespace(get_users=no_users)
        assert await client.get_bot_user_by_login("ghost") is None

    async def test_get_streams(self, client):
        async def get_streams(user_login):
            for login in user_login:
                if login == "live":
                    yield SimpleNamespace(user_login="live", title="stream")

        client._twitch = SimpleNamespace(get_streams=get_streams)
        result = await client.get_streams(["live", "offline"])
        assert result["live"] is not None
        assert result["offline"] is None
