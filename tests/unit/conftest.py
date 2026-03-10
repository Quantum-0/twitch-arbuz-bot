import pytest

# !!! ORDER IS IMPORTANT !!!
from tests.unit.fixtures.twitch_message import twitch_message_event_model, twitch_message_event_raw  # noqa
from pytest import fixture

from database.models import User
from twitch.state_manager import InMemoryStateManager


@fixture
def state_manager():
    return InMemoryStateManager()


@fixture
def send_message_mock():
    class SendMessageMock:
        def __init__(self):
            self._calls = []

        async def __call__(self, chat: User | str, message: str) -> None:
            self._calls.append((chat, message))

        def assert_sent(self, message: str, chat: str = None):
            if chat:
                assert any(
                    call[1] == message and call[0] == chat for call in self._calls
                ), self._calls
            else:
                assert any(call[1] == message for call in self._calls), self._calls

        def assert_not_sent(self, message: str = None, chat: str = None):
            if message is None:
                assert len(self._calls) == 0
                return
            if chat:
                assert not any(
                    call[1] == message and call[0] == chat for call in self._calls
                ), self._calls
            else:
                assert not any(call[1] == message for call in self._calls), self._calls

    return SendMessageMock()


#
#
# @pytest.mark.asyncio
# @pytest.fixture(scope='session')
# async def client(pg_engine: aiopg.sa.Engine) -> AsyncClient:
#     from app.main import app, startup_event
#
#     await startup_event()
#
#     settings_development = settings.DEVELOPMENT
#     settings.DEVELOPMENT = True
#     async with AsyncClient(
#         app=app, base_url='http://test', headers={'Authorization': 'Bearer dummy'}
#     ) as ac:
#         yield ac
#     settings.DEVELOPMENT = settings_development
