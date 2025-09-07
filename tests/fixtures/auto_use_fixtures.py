import asyncio

import pytest


@pytest.fixture(scope="session", autouse=True)
def event_loop():
    """Чтобы pytest-asyncio не ругался на разный цикл."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()