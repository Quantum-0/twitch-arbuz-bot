import pytest
from pytest import fixture
from pytest_asyncio import is_async_test

# !!! ORDER IS IMPORTANT !!!
from fixtures.auto_use_fixtures import event_loop  # noqa
from fixtures.twitch_message import twitch_message_event_raw, twitch_message_event_model  # noqa
from fixtures.database_fixtures import postgres_container, migrations, test_engine, db_session, test_user  # noqa
from fixtures.client_fixtures import session_override, client, user_auth_mock, test_user_cookie  # noqa

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
                assert any(call[1] == message and call[0] == chat for call in self._calls), self._calls
            else:
                assert any(call[1] == message for call in self._calls), self._calls

        def assert_not_sent(self, message: str = None, chat: str = None):
            if message is None:
                assert len(self._calls) == 0
                return
            if chat:
                assert not any(call[1] == message and call[0] == chat for call in self._calls), self._calls
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

def pytest_collection_modifyitems(items):
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)