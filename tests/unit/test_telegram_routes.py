# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Роут-тесты /api/user/telegram/*: настройки, connect-deep-link, unlink, disconnect."""

import inspect
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import routers.api.user.telegram as tg_routes
from database.models import TelegramSettings
from main import app


def _make_override(fake):
    def override():
        return fake

    return override


@contextmanager
def provide_overrides(route, fakes: dict):
    keys = []
    for name, fake in fakes.items():
        dep = inspect.signature(route).parameters[name].annotation.__metadata__[0].dependency
        app.dependency_overrides[dep] = _make_override(fake)
        keys.append(dep)
    try:
        yield
    finally:
        for dep in keys:
            app.dependency_overrides.pop(dep, None)


class FakeHttpResponse:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._body = body or {}

    @property
    def is_success(self) -> bool:
        return self.status_code < 400

    @property
    def text(self) -> str:
        return str(self._body)

    def json(self) -> dict[str, Any]:
        return self._body


class FakeTgHttpClient:
    def __init__(self) -> None:
        self.response: Any = FakeHttpResponse(body={"short_id": "abcd1234"})
        self.posts: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.posts.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def tg_http(monkeypatch) -> FakeTgHttpClient:
    fake = FakeTgHttpClient()
    monkeypatch.setattr(tg_routes.httpx, "AsyncClient", lambda *a, **kw: fake)  # noqa: ARG005
    return fake


@pytest.fixture
async def client(session_override):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def twitch_fake():
    return SimpleNamespace(
        subscribe_stream_online=AsyncMock(),
        subscribe_stream_offline=AsyncMock(),
        unsubscribe_stream_online=AsyncMock(),
        unsubscribe_stream_offline=AsyncMock(),
    )


@pytest.fixture
def mqtt_fake():
    return SimpleNamespace(publish=AsyncMock())


async def make_tg_settings(db_session, test_user, **kwargs) -> TelegramSettings:
    tg = TelegramSettings(user_id=test_user.id, **kwargs)
    db_session.add(tg)
    await db_session.commit()
    await db_session.refresh(test_user, attribute_names=["telegram"])
    return tg


async def get_tg(db_session, test_user) -> TelegramSettings | None:
    return (
        await db_session.execute(select(TelegramSettings).where(TelegramSettings.user_id == test_user.id))
    ).scalar_one_or_none()


class TestGetSettings:
    async def test_no_row_returns_defaults(self, client, user_auth_mock):
        resp = await client.get("/api/user/telegram/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_connected"] is False
        assert body["stream_notification_enabled"] is False
        assert body["stream_offline_behavior"] == "keep"
        assert body["stream_restart_behavior"] == "silent"
        assert body["clips_mode"] == "all"
        assert body["clips_delivery"] == "link"
        assert body["stickers_mode"] == "photo"
        assert body["clips_enabled"] is False
        assert body["stickers_enabled"] is False

    async def test_with_row_real_values(self, client, user_auth_mock, db_session, test_user):
        await make_tg_settings(
            db_session,
            test_user,
            stream_chat_id="-100123",
            stream_chat_title="Stream Chat",
            stream_notification_enabled=True,
            stickers_mode="document",
        )
        resp = await client.get("/api/user/telegram/settings")
        body = resp.json()
        assert body["is_connected"] is True
        assert body["stream_chat_id"] == "-100123"
        assert body["stream_chat_title"] == "Stream Chat"
        assert body["stream_notification_enabled"] is True
        assert body["stickers_mode"] == "document"


class TestUpdateSettings:
    async def test_clips_beta_gate_403(self, client, user_auth_mock):
        resp = await client.post("/api/user/telegram/settings", json={"clips_enabled": True})
        assert resp.status_code == 403
        assert "доступна не всем" in resp.json()["message"].lower()

    async def test_clips_enabled_for_beta_user(self, client, user_auth_mock, db_session, test_user):
        test_user.login_name = "quantum075"
        await db_session.commit()
        resp = await client.post("/api/user/telegram/settings", json={"clips_enabled": True})
        assert resp.status_code == 200
        tg = await get_tg(db_session, test_user)
        assert tg is not None
        assert tg.clips_enabled is True

    async def test_update_without_toggle_change_skips_subscriptions(
        self, client, user_auth_mock, db_session, test_user, twitch_fake
    ):
        route = tg_routes.update_telegram_settings
        with provide_overrides(route, {"twitch": twitch_fake}):
            resp = await client.post("/api/user/telegram/settings", json={"stream_message_template": "Стрим!"})
        assert resp.status_code == 200
        tg = await get_tg(db_session, test_user)
        assert tg.stream_message_template == "Стрим!"
        twitch_fake.subscribe_stream_online.assert_not_awaited()
        twitch_fake.unsubscribe_stream_online.assert_not_awaited()

    async def test_enable_without_chat_defers_subscription(
        self, client, user_auth_mock, db_session, test_user, twitch_fake
    ):
        route = tg_routes.update_telegram_settings
        with provide_overrides(route, {"twitch": twitch_fake}):
            resp = await client.post("/api/user/telegram/settings", json={"stream_notification_enabled": True})
        assert resp.status_code == 200
        tg = await get_tg(db_session, test_user)
        assert tg.stream_notification_enabled is True  # тогл сохранён
        twitch_fake.subscribe_stream_online.assert_not_awaited()

    async def test_enable_with_chat_subscribes(self, client, user_auth_mock, db_session, test_user, twitch_fake):
        await make_tg_settings(db_session, test_user, stream_chat_id="-100123")
        route = tg_routes.update_telegram_settings
        with provide_overrides(route, {"twitch": twitch_fake}):
            resp = await client.post("/api/user/telegram/settings", json={"stream_notification_enabled": True})
        assert resp.status_code == 200
        twitch_fake.subscribe_stream_online.assert_awaited_once()
        twitch_fake.subscribe_stream_offline.assert_awaited_once()
        tg = await get_tg(db_session, test_user)
        assert tg.stream_notification_enabled is True

    async def test_enable_subscription_failure_rolls_back(
        self, client, user_auth_mock, db_session, test_user, twitch_fake
    ):
        await make_tg_settings(db_session, test_user, stream_chat_id="-100123")
        twitch_fake.subscribe_stream_online.side_effect = RuntimeError("eventsub down")
        route = tg_routes.update_telegram_settings
        with provide_overrides(route, {"twitch": twitch_fake}):
            resp = await client.post("/api/user/telegram/settings", json={"stream_notification_enabled": True})
        assert resp.status_code == 502
        tg = await get_tg(db_session, test_user)
        assert tg.stream_notification_enabled is False  # откат тогла

    async def test_disable_unsubscribes(self, client, user_auth_mock, db_session, test_user, twitch_fake):
        await make_tg_settings(db_session, test_user, stream_chat_id="-100123", stream_notification_enabled=True)
        route = tg_routes.update_telegram_settings
        with provide_overrides(route, {"twitch": twitch_fake}):
            resp = await client.post("/api/user/telegram/settings", json={"stream_notification_enabled": False})
        assert resp.status_code == 200
        twitch_fake.unsubscribe_stream_online.assert_awaited_once()
        twitch_fake.unsubscribe_stream_offline.assert_awaited_once()
        tg = await get_tg(db_session, test_user)
        assert tg.stream_notification_enabled is False

    async def test_disable_unsubscribe_failure_still_200(
        self, client, user_auth_mock, db_session, test_user, twitch_fake
    ):
        await make_tg_settings(db_session, test_user, stream_chat_id="-100123", stream_notification_enabled=True)
        twitch_fake.unsubscribe_stream_online.side_effect = RuntimeError("boom")
        route = tg_routes.update_telegram_settings
        with provide_overrides(route, {"twitch": twitch_fake}):
            resp = await client.post("/api/user/telegram/settings", json={"stream_notification_enabled": False})
        assert resp.status_code == 200
        tg = await get_tg(db_session, test_user)
        assert tg.stream_notification_enabled is False


class TestConnectLink:
    async def test_clips_scope_beta_gate(self, client, user_auth_mock, tg_http):
        resp = await client.post("/api/user/telegram/connect", json={"scope": "clips", "chat_type": "channel"})
        assert resp.status_code == 403
        assert tg_http.posts == []

    async def test_service_unreachable_503(self, client, user_auth_mock, tg_http):
        tg_http.response = httpx.ConnectError("refused")
        resp = await client.post("/api/user/telegram/connect", json={"scope": "stream", "chat_type": "channel"})
        assert resp.status_code == 503

    async def test_service_error_502(self, client, user_auth_mock, tg_http):
        tg_http.response = FakeHttpResponse(status_code=500)
        resp = await client.post("/api/user/telegram/connect", json={"scope": "stream", "chat_type": "channel"})
        assert resp.status_code == 502

    async def test_no_short_id_502(self, client, user_auth_mock, tg_http):
        tg_http.response = FakeHttpResponse(body={})
        resp = await client.post("/api/user/telegram/connect", json={"scope": "stream", "chat_type": "group"})
        assert resp.status_code == 502

    async def test_success_returns_url(self, client, user_auth_mock, tg_http, test_user):
        tg_http.response = FakeHttpResponse(body={"short_id": "deadbeef"})
        resp = await client.post("/api/user/telegram/connect", json={"scope": "stickers", "chat_type": "private"})
        assert resp.status_code == 200
        assert resp.json()["url"] == "https://t.me/quantum0s_twitch_bot?start=deadbeef"
        sent = tg_http.posts[0]
        assert sent["url"].endswith("/api/connect")
        assert sent["json"] == {"user_id": test_user.id, "scope": "stickers", "chat_type": "private"}
        assert "X-Api-Key" in sent["headers"]


class TestUnlinkAccount:
    async def test_no_settings_row_208(self, client, user_auth_mock):
        resp = await client.post("/api/user/telegram/unlink-account")
        assert resp.status_code == 208

    async def test_no_bound_account_208(self, client, user_auth_mock, db_session, test_user):
        await make_tg_settings(db_session, test_user)
        resp = await client.post("/api/user/telegram/unlink-account")
        assert resp.status_code == 208

    async def test_unlink_clears_fields(self, client, user_auth_mock, db_session, test_user):
        await make_tg_settings(db_session, test_user, telegram_user_id="4242", telegram_username="tg_user")
        resp = await client.post("/api/user/telegram/unlink-account")
        assert resp.status_code == 200
        await db_session.refresh(test_user, attribute_names=["telegram"])
        assert test_user.telegram.telegram_user_id is None
        assert test_user.telegram.telegram_username is None
        # Привязки чатов не трогаются
        assert test_user.telegram.stream_chat_id is None


class TestDisconnectScope:
    async def test_invalid_scope_400(self, client, user_auth_mock, twitch_fake, mqtt_fake):
        route = tg_routes.disconnect_telegram_scope
        with provide_overrides(route, {"twitch": twitch_fake, "mqtt": mqtt_fake}):
            resp = await client.post("/api/user/telegram/disconnect/bogus")
        assert resp.status_code == 400

    async def test_no_settings_row_returns_success(self, client, user_auth_mock, twitch_fake, mqtt_fake):
        route = tg_routes.disconnect_telegram_scope
        with provide_overrides(route, {"twitch": twitch_fake, "mqtt": mqtt_fake}):
            resp = await client.post("/api/user/telegram/disconnect/stream")
        assert resp.status_code == 200
        mqtt_fake.publish.assert_not_awaited()

    async def test_chat_not_connected_noop(self, client, user_auth_mock, db_session, test_user, twitch_fake, mqtt_fake):
        await make_tg_settings(db_session, test_user, stream_chat_id=None)
        route = tg_routes.disconnect_telegram_scope
        with provide_overrides(route, {"twitch": twitch_fake, "mqtt": mqtt_fake}):
            resp = await client.post("/api/user/telegram/disconnect/stream")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Нечего отключать"
        mqtt_fake.publish.assert_not_awaited()

    async def test_disconnect_stream_full_cleanup(
        self, client, user_auth_mock, db_session, test_user, twitch_fake, mqtt_fake
    ):
        await make_tg_settings(
            db_session,
            test_user,
            stream_chat_id="-100123",
            stream_chat_type="channel",
            stream_chat_title="Stream Chat",
            stream_connected_at=datetime(2026, 1, 1),
            stream_notification_enabled=True,
            last_stream_message_id="55",
            twitch_to_tg_enabled=True,
            tg_to_twitch_enabled=True,
        )
        route = tg_routes.disconnect_telegram_scope
        with provide_overrides(route, {"twitch": twitch_fake, "mqtt": mqtt_fake}):
            resp = await client.post("/api/user/telegram/disconnect/stream")
        assert resp.status_code == 200
        tg = await get_tg(db_session, test_user)
        assert tg.stream_chat_id is None
        assert tg.stream_chat_type is None
        assert tg.stream_chat_title is None
        assert tg.stream_connected_at is None
        assert tg.stream_notification_enabled is False
        assert tg.last_stream_message_id is None
        assert tg.twitch_to_tg_enabled is False
        assert tg.tg_to_twitch_enabled is False
        mqtt_fake.publish.assert_awaited_once_with("telegram/leave_chat", {"chat_id": "-100123"})
        twitch_fake.unsubscribe_stream_online.assert_awaited_once()
        twitch_fake.unsubscribe_stream_offline.assert_awaited_once()

    async def test_mqtt_failure_still_success(
        self, client, user_auth_mock, db_session, test_user, twitch_fake, mqtt_fake
    ):
        await make_tg_settings(db_session, test_user, clips_chat_id="-100555")
        mqtt_fake.publish.side_effect = RuntimeError("mqtt down")
        route = tg_routes.disconnect_telegram_scope
        with provide_overrides(route, {"twitch": twitch_fake, "mqtt": mqtt_fake}):
            resp = await client.post("/api/user/telegram/disconnect/clips")
        assert resp.status_code == 200
        tg = await get_tg(db_session, test_user)
        assert tg.clips_chat_id is None

    async def test_unsubscribe_failure_still_success(
        self, client, user_auth_mock, db_session, test_user, twitch_fake, mqtt_fake
    ):
        await make_tg_settings(db_session, test_user, stream_chat_id="-100123")
        twitch_fake.unsubscribe_stream_online.side_effect = RuntimeError("boom")
        route = tg_routes.disconnect_telegram_scope
        with provide_overrides(route, {"twitch": twitch_fake, "mqtt": mqtt_fake}):
            resp = await client.post("/api/user/telegram/disconnect/stream")
        assert resp.status_code == 200
        tg = await get_tg(db_session, test_user)
        assert tg.stream_chat_id is None

    async def test_disconnect_stickers(self, client, user_auth_mock, db_session, test_user, twitch_fake, mqtt_fake):
        await make_tg_settings(
            db_session,
            test_user,
            stickers_chat_id="-100777",
            stickers_chat_type="group",
            stickers_chat_title="Stickers",
            stickers_enabled=True,
        )
        route = tg_routes.disconnect_telegram_scope
        with provide_overrides(route, {"twitch": twitch_fake, "mqtt": mqtt_fake}):
            resp = await client.post("/api/user/telegram/disconnect/stickers")
        assert resp.status_code == 200
        tg = await get_tg(db_session, test_user)
        assert tg.stickers_chat_id is None
        assert tg.stickers_enabled is False
        mqtt_fake.publish.assert_awaited_once_with("telegram/leave_chat", {"chat_id": "-100777"})
        twitch_fake.unsubscribe_stream_online.assert_not_awaited()
