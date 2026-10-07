"""Юнит-тесты handle_chat_connected: заполнение telegram_user_id для приватных чатов."""

from types import SimpleNamespace

import pytest

from services.telegram_integration import handle_chat_connected


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """Минимальная эмуляция AsyncSession для handle_chat_connected."""

    def __init__(self, tg):
        self.tg = tg
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, _stmt):
        return FakeResult(self.tg)

    def add(self, obj):
        self.tg = obj

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


def build_db(tg):
    session = FakeSession(tg)

    def factory():
        return session

    return session, factory


def build_tg(**overrides) -> SimpleNamespace:
    fields = {
        "stream_chat_id": None,
        "stream_chat_type": None,
        "stream_chat_title": None,
        "stream_connected_at": None,
        "clips_chat_id": None,
        "clips_chat_type": None,
        "clips_chat_title": None,
        "clips_connected_at": None,
        "stickers_chat_id": None,
        "stickers_chat_type": None,
        "stickers_chat_title": None,
        "stickers_connected_at": None,
        "stream_notification_enabled": False,
        "telegram_user_id": None,
        "user_id": 1,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


PAYLOAD = {"user_id": 1, "scope": "clips", "chat_id": "424242", "chat_type": "private", "chat_title": "Иван"}


@pytest.mark.asyncio
async def test_private_chat_fills_telegram_user_id() -> None:
    tg = build_tg()
    session, factory = build_db(tg)

    await handle_chat_connected(dict(PAYLOAD), factory)

    assert tg.telegram_user_id == "424242"
    assert tg.clips_chat_id == "424242"
    assert session.committed


@pytest.mark.asyncio
async def test_group_chat_does_not_touch_telegram_user_id() -> None:
    tg = build_tg()
    session, factory = build_db(tg)

    await handle_chat_connected(
        {**PAYLOAD, "chat_type": "supergroup", "chat_title": "Чат стримера"},
        factory,
    )

    assert tg.telegram_user_id is None
    assert tg.clips_chat_id == "424242"
    assert session.committed


@pytest.mark.asyncio
async def test_private_chat_overwrites_stale_telegram_user_id() -> None:
    """Повторная привязка приватного чата обновляет telegram_user_id (смена TG-аккаунта)."""
    tg = build_tg(telegram_user_id="111111")
    _, factory = build_db(tg)

    await handle_chat_connected(dict(PAYLOAD), factory)

    assert tg.telegram_user_id == "424242"
