import os
from typing import AsyncGenerator

import pytest
from alembic.config import Config as AlembicConfig
from docker.errors import DockerException
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer

from alembic import command
from database.models import User


@pytest.fixture(scope="session")
def postgres_container():
    """Поднимаем PostgreSQL в докере."""
    try:
        with PostgresContainer("postgres:15") as postgres:
            postgres.start()
            sync_url = postgres.get_connection_url()
            async_url = sync_url.replace(
                "postgresql+psycopg2://", "postgresql+asyncpg://"
            )
            old_sync_url = os.environ.get("DB_SYNC_URL")
            old_async_url = os.environ.get("DB_URL")
            os.environ["DB_SYNC_URL"] = sync_url
            os.environ["DB_URL"] = async_url
            yield postgres
            os.environ["DB_SYNC_URL"] = old_sync_url
            os.environ["DB_URL"] = old_async_url
    except DockerException:
        assert False, "Docker is not started"


@pytest.fixture(scope="session")
def migrations(postgres_container):
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_cfg.set_main_option(
        "sqlalchemy.url", postgres_container.get_connection_url()
    )
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(scope="function")
async def test_engine(postgres_container, migrations) -> AsyncGenerator:
    """Создаём движок к тестовой БД и накатываем миграции через Alembic."""
    url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, future=True, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
            await transaction.rollback()
            await connection.close()


@pytest.fixture
async def test_user(db_session):
    """Создаём тестового пользователя в БД с моком Twitch API."""

    # тестовые данные
    twitch_id = "123456"
    login_name = "test_user"
    profile_image_url = "https://example.com/avatar.png"
    access_token = "test-access-token"
    refresh_token = "test-refresh-token"
    followers_count = 142

    user = User(
        twitch_id=twitch_id,
        login_name=login_name,
        profile_image_url=profile_image_url,
        access_token=access_token,
        refresh_token=refresh_token,
        followers_count=followers_count,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user
