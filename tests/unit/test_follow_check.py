# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты services/follow_check.py: фоновая перепроверка follow на админа."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import services.follow_check as fc
from database.models import User
from services.follow_check import FOLLOW_CHECK_TTL_SECONDS, refresh_followed_to_admin
from tests.unit.fixtures.fake_redis import SessionCtx


@pytest.fixture
def cache_fake():
    return SimpleNamespaceCache()


class SimpleNamespaceCache:
    def __init__(self):
        self.get_str = AsyncMock(return_value=None)
        self.set_str = AsyncMock()


@pytest.fixture
def twitch_fake():
    return SimpleNamespaceTwitch()


class SimpleNamespaceTwitch:
    def __init__(self):
        self.check_user_follows_admin = AsyncMock(return_value=True)


@pytest.fixture
def follow_env(db_session, monkeypatch, cache_fake, twitch_fake):
    """Патчим AsyncSessionLocal и get_container на реальную сессию + фейки."""

    class FakeSessionFactory:
        def __call__(self) -> Any:
            return SessionCtx(db_session)

    container = SimpleNamespace(cache=lambda: cache_fake, twitch=lambda: twitch_fake)
    monkeypatch.setattr(fc, "AsyncSessionLocal", FakeSessionFactory())
    monkeypatch.setattr(fc, "get_container", lambda: container)
    return cache_fake, twitch_fake


async def make_admin(db_session) -> User:
    admin = User(
        twitch_id="999001",
        login_name="quantum075",
        profile_image_url="https://example.com/admin.png",
        access_token="admin-access-token",
        refresh_token="admin-refresh-token",
    )
    db_session.add(admin)
    await db_session.commit()
    return admin


class TestRefreshFollowedToAdmin:
    async def test_throttled_skips_everything(self, follow_env, test_user):
        cache_fake, twitch_fake = follow_env
        cache_fake.get_str.return_value = "1"
        await refresh_followed_to_admin(test_user.id)
        cache_fake.set_str.assert_not_awaited()
        twitch_fake.check_user_follows_admin.assert_not_awaited()

    async def test_throttle_key_set_before_api_call(self, follow_env, test_user):
        cache_fake, _ = follow_env
        await refresh_followed_to_admin(test_user.id)
        cache_fake.set_str.assert_awaited_once_with(
            f"follow_admin_check:{test_user.id}", "1", ttl=FOLLOW_CHECK_TTL_SECONDS
        )

    async def test_no_admin_in_db_skips(self, follow_env, test_user):
        _, twitch_fake = follow_env
        await refresh_followed_to_admin(test_user.id)
        twitch_fake.check_user_follows_admin.assert_not_awaited()

    async def test_unknown_user_id_skips(self, follow_env, db_session, test_user):
        _, twitch_fake = follow_env
        await make_admin(db_session)
        await refresh_followed_to_admin(test_user.id + 999999)
        twitch_fake.check_user_follows_admin.assert_not_awaited()

    async def test_twitch_error_keeps_old_value(self, follow_env, db_session, test_user):
        _, twitch_fake = follow_env
        await make_admin(db_session)
        twitch_fake.check_user_follows_admin.side_effect = RuntimeError("api down")
        await refresh_followed_to_admin(test_user.id)
        user = (await db_session.execute(select(User).where(User.id == test_user.id))).scalar_one()
        assert user.followed_to_admin is None

    @pytest.mark.parametrize("follows", [True, False])
    async def test_success_updates_flag(self, follow_env, db_session, test_user, follows):
        _, twitch_fake = follow_env
        admin = await make_admin(db_session)
        twitch_fake.check_user_follows_admin.return_value = follows
        await refresh_followed_to_admin(test_user.id)
        twitch_fake.check_user_follows_admin.assert_awaited_once_with(admin.twitch_id, test_user.twitch_id)
        user = (await db_session.execute(select(User).where(User.id == test_user.id))).scalar_one()
        assert user.followed_to_admin is follows
