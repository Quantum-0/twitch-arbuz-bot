import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from container import Container
from database.models import Base
from main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    session_factory = sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def container():
    container = Container()

    # Mock external services
    container.twitch.override(AsyncMock())
    container.mqtt.override(AsyncMock())

    mock_redis = AsyncMock()
    # Mock for init_redis Resource
    container.redis.override(mock_redis)
    container.binary_redis.override(mock_redis)

    container.state_manager.override(AsyncMock())
    container.cache.override(AsyncMock())

    container.ai.override(AsyncMock())
    container.s3.override(AsyncMock())
    container.memealerts.override(AsyncMock())

    return container


@pytest.fixture
def mock_twitch(container):
    return container.twitch()


@pytest.fixture
def mock_mqtt(container):
    return container.mqtt()


@pytest_asyncio.fixture
async def client(container, test_engine):
    session_factory = sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    with container.db_session_factory.override(session_factory):
        app.container = container

        # Override lifespan to avoid real Redis/MQTT/Twitch initialization
        @asynccontextmanager
        async def mock_lifespan(app):
            yield

        original_lifespan = app.router.lifespan_context
        app.router.lifespan_context = mock_lifespan

        with TestClient(app) as c:
            yield c

        app.router.lifespan_context = original_lifespan
