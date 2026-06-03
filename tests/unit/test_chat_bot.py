import sys
import types
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

# twitch.chat.bot only needs this import for typing in this test. Stubbing it keeps
# the regression test focused on SQLAlchemy session lifecycle and avoids importing
# Twitch API helpers that require optional runtime-only packages.
twitch_client_stub = types.ModuleType("twitch.client.twitch")
twitch_client_stub.Twitch = type("Twitch", (), {})
sys.modules.setdefault("twitch.client.twitch", twitch_client_stub)

from database.models import User  # noqa: E402
from twitch.chat.bot import ChatBot  # noqa: E402
from twitch.state_manager import InMemoryStateManager  # noqa: E402


class _ScalarResult:
    def __init__(self, values: list[Any]):
        self._values = values

    def all(self) -> list[Any]:
        return self._values


class _ExecuteResult:
    def __init__(self, values: list[Any]):
        self._values = values

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._values)


class _TrackingSession:
    def __init__(self, result_values: list[Any]):
        self._result_values = result_values
        self.closed = False
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        self.closed = True

    async def execute(self, _query):
        if self.closed:
            raise AssertionError("DB session was reused after leaving its context manager")
        return _ExecuteResult(self._result_values)

    async def commit(self):
        if self.closed:
            raise AssertionError("DB session was committed after leaving its context manager")
        self.committed = True


class _SessionFactory:
    def __init__(self, results: list[list[Any]]):
        self._results = results
        self.sessions: list[_TrackingSession] = []

    def __call__(self) -> _TrackingSession:
        session = _TrackingSession(self._results[len(self.sessions)])
        self.sessions.append(session)
        return session


@dataclass
class _Subscription:
    type: str
    condition: dict[str, str]
    id: str = "sub-id"


class _TwitchMock:
    async def get_subscriptions(self) -> list[_Subscription]:
        return []

    async def subscribe_chat_messages(
        self,
        *channels: User,
    ) -> AsyncIterator[tuple[User, bool, dict[str, str]]]:
        for channel in channels:
            yield channel, False, {"message": "subscription missing proper authorization"}

    async def unsubscribe_event_sub(self, _sub_id: str) -> None:
        raise AssertionError("No subscriptions should be removed in this test")


@pytest.mark.asyncio
async def test_update_bot_channels_does_not_reuse_closed_session():
    user = User(id=42, twitch_id="123", login_name="streamer")
    session_factory = _SessionFactory([[user], [user.id]])
    bot = ChatBot(db_session_factory=session_factory, state_manager=InMemoryStateManager())
    bot._twitch = _TwitchMock()

    await bot.update_bot_channels()

    if len(session_factory.sessions) != 2:
        raise AssertionError("Expected one session for reading and one session for cleanup update")
    if not all(session.closed for session in session_factory.sessions):
        raise AssertionError("Expected all opened sessions to be closed")
    if not session_factory.sessions[1].committed:
        raise AssertionError("Expected the cleanup update session to be committed")
