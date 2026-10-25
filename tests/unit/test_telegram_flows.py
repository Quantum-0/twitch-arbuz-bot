# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""DB-тесты функций services/telegram_integration.py.

handle_chat_connected (приватные чаты) покрыт в test_telegram_integration.py —
здесь stream-флоу, результаты доставок, сверка подписок и очистка привязок.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import sqlalchemy as sa

from database.models import TelegramSettings, User
from services.telegram_integration import (
    _ensure_stream_subscriptions,
    _reconcile_user_subs,
    clear_telegram_chat_binding,
    handle_chat_connected,
    handle_chat_disconnected,
    handle_telegram_result,
    reconcile_stream_subscriptions,
)


class SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


def fake_twitch(**methods):
    twitch = Mock()
    twitch.subscribe_stream_online = AsyncMock()
    twitch.subscribe_stream_offline = AsyncMock()
    twitch.unsubscribe_stream_online = AsyncMock()
    twitch.unsubscribe_stream_offline = AsyncMock()
    twitch.get_subscriptions = AsyncMock(return_value=[])
    for name, value in methods.items():
        setattr(twitch, name, value)
    return twitch


@pytest.fixture
def twitch_container(monkeypatch):
    """Подмена container_runtime.get_container → контейнер с фейковым Twitch."""
    twitch = fake_twitch()
    holder = SimpleNamespace(twitch=lambda: twitch)
    monkeypatch.setattr("container_runtime.get_container", lambda: holder)
    return twitch


async def add_tg_settings(db_session, user_id, **overrides):
    tg = TelegramSettings(user_id=user_id)
    for key, value in overrides.items():
        setattr(tg, key, value)
    db_session.add(tg)
    await db_session.commit()
    return tg


async def fetch_tg(db_session, user_id):
    return (
        await db_session.execute(sa.select(TelegramSettings).where(TelegramSettings.user_id == user_id))
    ).scalar_one()


# ── handle_chat_connected: stream-флоу ────────────────────────────────────


async def test_chat_connected_stream_scope_saves_binding(db_session, test_user, twitch_container):
    payload = {
        "user_id": test_user.id,
        "scope": "stream",
        "chat_id": 424242,
        "chat_type": "supergroup",
        "chat_title": "Чат стримера",
    }

    await handle_chat_connected(payload, lambda: SessionCtx(db_session))

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stream_chat_id == "424242"
    assert tg.stream_chat_type == "supergroup"
    assert tg.stream_chat_title == "Чат стримера"
    assert tg.stream_connected_at is not None
    # Уведомления выключены — подписки не создаём
    twitch_container.subscribe_stream_online.assert_not_awaited()


async def test_chat_connected_stream_notifications_enabled_subscribes(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stream_notification_enabled=True)
    payload = {"user_id": test_user.id, "scope": "stream", "chat_id": "100", "chat_type": "group", "chat_title": "t"}

    await handle_chat_connected(payload, lambda: SessionCtx(db_session))

    twitch_container.subscribe_stream_online.assert_awaited_once()
    twitch_container.subscribe_stream_offline.assert_awaited_once()
    assert twitch_container.subscribe_stream_online.await_args.args[0] is not None


async def test_chat_connected_unknown_scope_binds_nothing(db_session, test_user, twitch_container):
    payload = {"user_id": test_user.id, "scope": "unknown", "chat_id": "1", "chat_type": "private", "chat_title": ""}

    await handle_chat_connected(payload, lambda: SessionCtx(db_session))

    # Строка могла быть заflush'ена, но ни одна привязка не заполнена (return до commit)
    rows = (
        await db_session.execute(sa.select(TelegramSettings).where(TelegramSettings.user_id == test_user.id))
    ).scalar_one_or_none()
    if rows is not None:
        assert rows.stream_chat_id is None
        assert rows.clips_chat_id is None
        assert rows.stickers_chat_id is None
        assert rows.telegram_user_id is None


async def test_chat_connected_incomplete_payload_no_writes(db_session, test_user):
    await handle_chat_connected({"user_id": test_user.id, "scope": "stream"}, lambda: SessionCtx(db_session))

    rows = (
        await db_session.execute(sa.select(TelegramSettings).where(TelegramSettings.user_id == test_user.id))
    ).scalar_one_or_none()
    assert rows is None


async def test_chat_connected_stickers_scope(db_session, test_user, twitch_container):
    payload = {"user_id": test_user.id, "scope": "stickers", "chat_id": "7", "chat_type": "channel", "chat_title": "s"}

    await handle_chat_connected(payload, lambda: SessionCtx(db_session))

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stickers_chat_id == "7"
    assert tg.stickers_chat_type == "channel"


# ── handle_telegram_result ────────────────────────────────────────────────


async def test_result_stream_online_saves_message_id(db_session, test_user):
    await add_tg_settings(db_session, test_user.id)
    payload = {"request_id": f"stream_online:{test_user.id}", "success": True, "message_id": "m-1"}

    await handle_telegram_result(payload, lambda: SessionCtx(db_session))

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.last_stream_message_id == "m-1"


async def test_result_stream_offline_saves_message_id(db_session, test_user):
    await add_tg_settings(db_session, test_user.id)
    payload = {"request_id": f"stream_offline:{test_user.id}", "success": True, "message_id": "m-2"}

    await handle_telegram_result(payload, lambda: SessionCtx(db_session))

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.last_stream_offline_message_id == "m-2"


async def test_result_failed_delivery_not_saved(db_session, test_user):
    await add_tg_settings(db_session, test_user.id)
    payload = {"request_id": f"stream_online:{test_user.id}", "success": False, "error": "chat not found"}

    await handle_telegram_result(payload, lambda: SessionCtx(db_session))

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.last_stream_message_id is None


async def test_result_non_stream_request_id_ignored(db_session, test_user):
    await add_tg_settings(db_session, test_user.id)
    payload = {"request_id": "clip:123", "success": True, "message_id": "m-3"}

    await handle_telegram_result(payload, lambda: SessionCtx(db_session))

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.last_stream_message_id is None


async def test_result_invalid_payload_ignored(db_session, test_user):
    await handle_telegram_result({"unexpected": "shape"}, lambda: SessionCtx(db_session))

    rows = (
        await db_session.execute(sa.select(TelegramSettings).where(TelegramSettings.user_id == test_user.id))
    ).scalar_one_or_none()
    assert rows is None


async def test_result_non_numeric_user_id_ignored(db_session, test_user):
    await add_tg_settings(db_session, test_user.id)
    payload = {"request_id": "stream_online:not-a-number", "success": True, "message_id": "m-4"}

    await handle_telegram_result(payload, lambda: SessionCtx(db_session))

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.last_stream_message_id is None


# ── _ensure_stream_subscriptions ──────────────────────────────────────────


async def test_ensure_stream_subscriptions_success(db_session, test_user, twitch_container):
    result = await _ensure_stream_subscriptions(test_user.id, lambda: SessionCtx(db_session))

    assert result is True
    twitch_container.subscribe_stream_online.assert_awaited_once()
    twitch_container.subscribe_stream_offline.assert_awaited_once()


async def test_ensure_stream_subscriptions_user_missing(db_session, twitch_container):
    result = await _ensure_stream_subscriptions(99999, lambda: SessionCtx(db_session))

    assert result is False
    twitch_container.subscribe_stream_online.assert_not_awaited()


async def test_ensure_stream_subscriptions_twitch_failure(db_session, test_user, twitch_container):
    twitch_container.subscribe_stream_online = AsyncMock(side_effect=RuntimeError("twitch down"))

    result = await _ensure_stream_subscriptions(test_user.id, lambda: SessionCtx(db_session))

    assert result is False


# ── _reconcile_user_subs ──────────────────────────────────────────────────


async def test_reconcile_user_subs_creates_missing(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stream_notification_enabled=True)

    failed = await _reconcile_user_subs(
        test_user,
        has_online=False,
        has_offline=False,
        twitch=twitch_container,
        db_session_factory=lambda: SessionCtx(db_session),
    )

    assert failed is False
    twitch_container.subscribe_stream_online.assert_awaited_once()
    twitch_container.subscribe_stream_offline.assert_awaited_once()
    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stream_notification_enabled is True


async def test_reconcile_user_subs_online_failure_disables_flag(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stream_notification_enabled=True)
    twitch_container.subscribe_stream_online = AsyncMock(side_effect=RuntimeError("boom"))

    failed = await _reconcile_user_subs(
        test_user,
        has_online=False,
        has_offline=False,
        twitch=twitch_container,
        db_session_factory=lambda: SessionCtx(db_session),
    )

    assert failed is True
    # При сбое online offline даже не пытаемся создавать
    twitch_container.subscribe_stream_offline.assert_not_awaited()
    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stream_notification_enabled is False


async def test_reconcile_user_subs_offline_failure_disables_flag(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stream_notification_enabled=True)
    twitch_container.subscribe_stream_offline = AsyncMock(side_effect=RuntimeError("boom"))

    failed = await _reconcile_user_subs(
        test_user,
        has_online=True,
        has_offline=False,
        twitch=twitch_container,
        db_session_factory=lambda: SessionCtx(db_session),
    )

    assert failed is True
    twitch_container.subscribe_stream_online.assert_not_awaited()  # online уже есть
    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stream_notification_enabled is False


# ── reconcile_stream_subscriptions ────────────────────────────────────────


def sub(sub_type, broadcaster_id):
    return SimpleNamespace(type=sub_type, condition={"broadcaster_user_id": broadcaster_id})


async def test_reconcile_no_users_early_return(db_session, twitch_container):
    await reconcile_stream_subscriptions(lambda: SessionCtx(db_session))

    twitch_container.get_subscriptions.assert_not_awaited()


async def test_reconcile_all_present_no_calls(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stream_notification_enabled=True, stream_chat_id="100")
    twitch_container.get_subscriptions = AsyncMock(
        return_value=[sub("stream.online", test_user.twitch_id), sub("stream.offline", test_user.twitch_id)]
    )

    await reconcile_stream_subscriptions(lambda: SessionCtx(db_session))

    twitch_container.subscribe_stream_online.assert_not_awaited()
    twitch_container.subscribe_stream_offline.assert_not_awaited()


async def test_reconcile_missing_subs_recreated(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stream_notification_enabled=True, stream_chat_id="100")
    twitch_container.get_subscriptions = AsyncMock(return_value=[sub("stream.online", test_user.twitch_id)])

    await reconcile_stream_subscriptions(lambda: SessionCtx(db_session))

    twitch_container.subscribe_stream_online.assert_not_awaited()
    twitch_container.subscribe_stream_offline.assert_awaited_once()


async def test_reconcile_failure_disables_notification(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stream_notification_enabled=True, stream_chat_id="100")
    twitch_container.get_subscriptions = AsyncMock(return_value=[])
    twitch_container.subscribe_stream_online = AsyncMock(side_effect=RuntimeError("boom"))

    await reconcile_stream_subscriptions(lambda: SessionCtx(db_session))

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stream_notification_enabled is False


async def test_reconcile_ignores_foreign_subscriptions(db_session, test_user, twitch_container):
    """Подписки чужих broadcaster'ов не засчитываются нашему юзеру."""
    await add_tg_settings(db_session, test_user.id, stream_notification_enabled=True, stream_chat_id="100")
    twitch_container.get_subscriptions = AsyncMock(
        return_value=[sub("stream.online", "999999"), sub("stream.offline", "999999")]
    )

    await reconcile_stream_subscriptions(lambda: SessionCtx(db_session))

    twitch_container.subscribe_stream_online.assert_awaited_once()
    twitch_container.subscribe_stream_offline.assert_awaited_once()


async def test_reconcile_get_subscriptions_error_early_return(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stream_notification_enabled=True, stream_chat_id="100")
    twitch_container.get_subscriptions = AsyncMock(side_effect=RuntimeError("api down"))

    await reconcile_stream_subscriptions(lambda: SessionCtx(db_session))

    twitch_container.subscribe_stream_online.assert_not_awaited()
    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stream_notification_enabled is True


# ── clear_telegram_chat_binding / handle_chat_disconnected ────────────────


async def test_clear_binding_by_chat_id_stream(db_session, test_user, twitch_container):
    await add_tg_settings(
        db_session,
        test_user.id,
        stream_chat_id="100",
        stream_chat_type="group",
        stream_chat_title="t",
        stream_notification_enabled=True,
        last_stream_message_id="m-1",
        twitch_to_tg_enabled=True,
        tg_to_twitch_enabled=True,
    )

    scope = await clear_telegram_chat_binding(lambda: SessionCtx(db_session), chat_id="100")

    assert scope == "stream"
    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stream_chat_id is None
    assert tg.stream_connected_at is None
    assert tg.stream_notification_enabled is False
    assert tg.last_stream_message_id is None
    assert tg.twitch_to_tg_enabled is False
    assert tg.tg_to_twitch_enabled is False
    twitch_container.unsubscribe_stream_online.assert_awaited_once()
    twitch_container.unsubscribe_stream_offline.assert_awaited_once()


async def test_clear_binding_clips_disables_clips(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, clips_chat_id="200", clips_enabled=True)

    scope = await clear_telegram_chat_binding(lambda: SessionCtx(db_session), chat_id="200")

    assert scope == "clips"
    tg = await fetch_tg(db_session, test_user.id)
    assert tg.clips_chat_id is None
    assert tg.clips_enabled is False
    # Stream-подписки не трогаем
    twitch_container.unsubscribe_stream_online.assert_not_awaited()


async def test_clear_binding_stickers(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stickers_chat_id="300", stickers_enabled=True)

    scope = await clear_telegram_chat_binding(lambda: SessionCtx(db_session), chat_id="300")

    assert scope == "stickers"
    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stickers_chat_id is None
    assert tg.stickers_enabled is False


async def test_clear_binding_scope_chat_id_mismatch_skipped(db_session, test_user, twitch_container):
    """Юзер переподключил чат: старый payload с чужим chat_id не должен затирать новую привязку."""
    await add_tg_settings(db_session, test_user.id, stream_chat_id="new-chat")

    scope = await clear_telegram_chat_binding(
        lambda: SessionCtx(db_session), chat_id="old-chat", user_id=test_user.id, scope="stream"
    )

    assert scope is None
    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stream_chat_id == "new-chat"


async def test_clear_binding_unknown_chat_returns_none(db_session, test_user, twitch_container):
    assert await clear_telegram_chat_binding(lambda: SessionCtx(db_session), chat_id="404") is None


async def test_chat_disconnected_without_chat_id_noop(db_session, test_user, twitch_container):
    await handle_chat_disconnected({"user_id": test_user.id, "scope": "stream"}, lambda: SessionCtx(db_session))

    rows = (
        await db_session.execute(sa.select(TelegramSettings).where(TelegramSettings.user_id == test_user.id))
    ).scalar_one_or_none()
    assert rows is None


async def test_chat_disconnected_delegates_to_clear(db_session, test_user, twitch_container):
    await add_tg_settings(db_session, test_user.id, stream_chat_id="100", stream_notification_enabled=True)

    await handle_chat_disconnected(
        {"chat_id": "100", "user_id": test_user.id, "scope": "stream"}, lambda: SessionCtx(db_session)
    )

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.stream_chat_id is None
    assert tg.stream_notification_enabled is False
    twitch_container.unsubscribe_stream_online.assert_awaited_once()


# ── User-строка создаётся лениво ──────────────────────────────────────────


async def test_chat_connected_creates_missing_settings_row(db_session, test_user, twitch_container):
    payload = {"user_id": test_user.id, "scope": "clips", "chat_id": "5", "chat_type": "private", "chat_title": "p"}

    await handle_chat_connected(payload, lambda: SessionCtx(db_session))

    tg = await fetch_tg(db_session, test_user.id)
    assert tg.clips_chat_id == "5"
    # Приватный чат → telegram_user_id заполнен
    assert tg.telegram_user_id == "5"
    assert tg.user_id == test_user.id
    # Строка User не дублируется
    users = (await db_session.execute(sa.select(User).where(User.id == test_user.id))).scalars().all()
    assert len(users) == 1
