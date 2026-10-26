# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Роут-тесты service_routes: /login, /logout, /login-callback (создание/обновление
пользователя) и пост-логин reconcile login_callback_task."""

import asyncio
import inspect
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import routers.web.service_routes as sr
from database.models import User
from main import app
from tests.unit.fixtures.fake_redis import SessionCtx


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


@pytest.fixture
async def client(session_override) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def make_twitch(
    tokens: tuple[str, str, int] | None = ("new-access", "new-refresh", 7200),
    user_info: SimpleNamespace | None = None,
) -> SimpleNamespace:
    if user_info is None:
        user_info = SimpleNamespace(id="999888", login="new_user", profile_image_url="https://x/y.png")
    return SimpleNamespace(
        get_user_access_refresh_tokens_by_authorization_code=AsyncMock(return_value=tokens),
        get_self=AsyncMock(return_value=user_info),
        get_followers=AsyncMock(return_value=SimpleNamespace(total=777)),
        set_bot_moder=AsyncMock(),
        get_subscriptions=AsyncMock(return_value=[]),
        subscribe_raid=AsyncMock(),
        unsubscribe_event_sub=AsyncMock(),
        subscribe_stream_online=AsyncMock(),
        subscribe_stream_offline=AsyncMock(),
        subscribe_reward=AsyncMock(),
    )


@pytest.fixture(autouse=True)
def no_background_task(monkeypatch):
    """Перехват фоновой пост-логин задачи, чтобы не утекала в общий loop."""
    started: list[User] = []

    async def fake_task(user: User) -> None:
        started.append(user)

    monkeypatch.setattr(sr, "_run_login_callback_task", fake_task)
    return started


class TestSimpleRoutes:
    async def test_favicon(self, client):
        resp = await client.get("/favicon.ico")
        assert resp.status_code == 200
        assert "image" in resp.headers["content-type"]
        assert resp.headers["cache-control"] == "public, max-age=604800"

    async def test_login_redirects(self, client):
        resp = await client.get("/login", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"].startswith("https://")

    async def test_logout_clears_session(self, client, test_user_cookie):
        resp = await client.get("/logout", follow_redirects=False, cookies=test_user_cookie)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/"
        # Сессионная cookie очищена
        assert "session" not in {c.name for c in resp.cookies.values()}


class TestLoginCallback:
    async def test_new_user_created(self, client, db_session, no_background_task):
        twitch = make_twitch()
        with provide_overrides(sr.callback, {"twitch": twitch}):
            resp = await client.get("/login-callback", params={"code": "abc"}, follow_redirects=False)

        assert resp.status_code == 307
        assert resp.headers["location"] == "/panel"
        # Пользователь создан с токенами
        result = await db_session.execute(sa_select_user("999888"))
        user = result.scalar_one()
        assert user.login_name == "new_user"
        assert user.twitch_token_expires_at is not None
        assert "user_id" in resp.cookies.get("session", "") or resp.cookies

        # Фоновая задача запущена с созданным пользователем
        assert len(no_background_task) == 1

    async def test_existing_user_updated(self, client, db_session, test_user, no_background_task):
        twitch = make_twitch(
            tokens=("fresh-access", "fresh-refresh", 3600),
            user_info=SimpleNamespace(id=test_user.twitch_id, login="renamed", profile_image_url="https://x/new.png"),
        )
        with provide_overrides(sr.callback, {"twitch": twitch}):
            resp = await client.get("/login-callback", params={"code": "abc"}, follow_redirects=False)

        assert resp.status_code == 307
        await db_session.refresh(test_user, attribute_names=["login_name", "profile_image_url"])
        assert test_user.login_name == "renamed"
        assert test_user.profile_image_url == "https://x/new.png"

    async def test_no_tokens_redirect_home(self, client, db_session, no_background_task):
        twitch = make_twitch(tokens=None)
        with provide_overrides(sr.callback, {"twitch": twitch}):
            resp = await client.get("/login-callback", params={"code": "abc"}, follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/"
        assert no_background_task == []


def sa_select_user(twitch_id: str):
    import sqlalchemy as sa

    return sa.select(User).filter_by(twitch_id=twitch_id)


def make_sub(sub_id: str, sub_type: str, condition: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(id=sub_id, type=sub_type, condition=condition, status="enabled")


class TestLoginCallbackTask:
    """Пост-логин reconcile: подписки/отписки по состоянию пользователя."""

    @staticmethod
    async def make_user_with(
        db_session, test_user, *, raid=False, telegram_stream=False, ma_reward=None, ai_reward=None
    ):
        test_user.settings.enable_shoutout_on_raid = raid
        if ma_reward is not None:
            test_user.memealerts.memealerts_reward = ma_reward
        if ai_reward is not None:
            test_user.settings.ai_sticker_reward_id = ai_reward
        if telegram_stream:
            from database.models import TelegramSettings

            db_session.add(
                TelegramSettings(user_id=test_user.id, stream_chat_id="-100", stream_notification_enabled=True)
            )
        await db_session.commit()
        await db_session.refresh(test_user, attribute_names=["settings", "memealerts", "telegram"])
        return test_user

    async def run_task(self, db_session, twitch, user):
        await sr.login_callback_task(user, twitch, lambda: SessionCtx(db_session))

    async def test_followers_updated_and_bot_mod(self, db_session, test_user):
        user = await self.make_user_with(db_session, test_user)
        twitch = make_twitch()
        twitch.get_followers = AsyncMock(return_value=SimpleNamespace(total=555))

        await self.run_task(db_session, twitch, user)

        twitch.set_bot_moder.assert_awaited_once()
        await db_session.refresh(user, attribute_names=["followers_count"])
        assert user.followers_count == 555

    async def test_raid_subscription_reconcile(self, db_session, test_user):
        user = await self.make_user_with(db_session, test_user, raid=True)
        twitch = make_twitch()
        twitch.get_subscriptions = AsyncMock(return_value=[])

        await self.run_task(db_session, twitch, user)
        twitch.subscribe_raid.assert_awaited_once()

    async def test_raid_unsubscribe_when_disabled(self, db_session, test_user):
        user = await self.make_user_with(db_session, test_user, raid=False)
        twitch = make_twitch()
        twitch.get_subscriptions = AsyncMock(
            return_value=[make_sub("s1", "channel.raid", {"to_broadcaster_user_id": test_user.twitch_id})]
        )

        await self.run_task(db_session, twitch, user)
        twitch.unsubscribe_event_sub.assert_awaited_once_with("s1")
        twitch.subscribe_raid.assert_not_awaited()

    async def test_stream_subs_created_when_missing(self, db_session, test_user):
        user = await self.make_user_with(db_session, test_user, telegram_stream=True)
        twitch = make_twitch()
        twitch.get_subscriptions = AsyncMock(return_value=[])

        await self.run_task(db_session, twitch, user)
        twitch.subscribe_stream_online.assert_awaited_once()
        twitch.subscribe_stream_offline.assert_awaited_once()

    async def test_stream_subscribe_failure_disables_toggle(self, db_session, test_user):
        from database.models import TelegramSettings

        user = await self.make_user_with(db_session, test_user, telegram_stream=True)
        twitch = make_twitch()
        twitch.get_subscriptions = AsyncMock(return_value=[])
        twitch.subscribe_stream_online = AsyncMock(side_effect=RuntimeError("twitch down"))

        await self.run_task(db_session, twitch, user)

        result = await db_session.execute(
            __import__("sqlalchemy").select(TelegramSettings).where(TelegramSettings.user_id == user.id)
        )
        tg = result.scalar_one()
        assert tg.stream_notification_enabled is False

    async def test_stream_subs_removed_when_disabled(self, db_session, test_user):
        user = await self.make_user_with(db_session, test_user, telegram_stream=False)
        twitch = make_twitch()
        twitch.get_subscriptions = AsyncMock(
            return_value=[
                make_sub("s1", "stream.online", {"broadcaster_user_id": test_user.twitch_id}),
                make_sub("s2", "stream.offline", {"broadcaster_user_id": test_user.twitch_id}),
            ]
        )

        await self.run_task(db_session, twitch, user)
        assert {c.args[0] for c in twitch.unsubscribe_event_sub.await_args_list} == {"s1", "s2"}

    async def test_extra_reward_subs_removed(self, db_session, test_user):
        user = await self.make_user_with(db_session, test_user)
        twitch = make_twitch()
        twitch.get_subscriptions = AsyncMock(
            return_value=[
                make_sub(
                    "s1",
                    "channel.channel_points_custom_reward_redemption.add",
                    {"broadcaster_user_id": test_user.twitch_id, "reward_id": "stale-reward"},
                )
            ]
        )

        await self.run_task(db_session, twitch, user)
        twitch.unsubscribe_event_sub.assert_awaited_once_with("s1")

    async def test_missing_reward_subs_created(self, db_session, test_user):
        from uuid import uuid4

        ma_reward, ai_reward = uuid4(), uuid4()
        user = await self.make_user_with(db_session, test_user, ma_reward=ma_reward, ai_reward=ai_reward)
        twitch = make_twitch()
        twitch.get_subscriptions = AsyncMock(return_value=[])

        await self.run_task(db_session, twitch, user)
        assert {c.args[1] for c in twitch.subscribe_reward.await_args_list} == {ma_reward, ai_reward}

    async def test_run_task_swallows_exceptions(self, test_user):
        """Обёртка логирует ошибку, не роняя вызывающий код."""
        user = test_user

        async def failing(u):
            raise RuntimeError("x")

        orig = sr.login_callback_task
        sr.login_callback_task = failing
        try:
            await sr._run_login_callback_task(user)  # не падает
        finally:
            sr.login_callback_task = orig


class TestBackgroundTaskNoLeak:
    async def test_callback_task_not_leaked(self):
        """Все фоновые задачи тестов завершены (нет зависших корутин)."""
        me = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
        assert pending == []
