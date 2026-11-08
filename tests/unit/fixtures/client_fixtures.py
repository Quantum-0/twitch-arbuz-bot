import json
from base64 import b64encode
from collections.abc import AsyncGenerator

import itsdangerous
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from dependencies import get_db
from main import app
from routers.security_helpers import user_auth, user_auth_optional

# Тесты едут с loop_scope="session" (conftest.py) — async-фикстуры должны
# использовать тот же цикл, иначе коннекты БД окажутся на чужом loop.


@pytest.fixture(scope="function", autouse=True)
def session_override(db_session):
    async def get_session_override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = get_session_override


@pytest.fixture(scope="function")
def user_auth_mock(db_session, test_user):  # noqa: ARG001
    app.dependency_overrides[user_auth] = lambda: test_user
    app.dependency_overrides[user_auth_optional] = lambda: test_user
    yield test_user
    # Иначе override утекает в соседние тесты, ждущие 401.
    app.dependency_overrides.pop(user_auth, None)
    app.dependency_overrides.pop(user_auth_optional, None)


@pytest.fixture(scope="function")
def test_user_cookie(test_user) -> dict[str, str]:
    # Ищем SessionMiddleware по классу: add_middleware вставляет в начало
    # списка, и user_middleware[0] может оказаться любым другим middleware.
    session_mw = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
    signer = itsdangerous.TimestampSigner(str(session_mw.kwargs["secret_key"]))
    cookie = signer.sign(b64encode(json.dumps({"user_id": test_user.twitch_id}).encode("utf-8")))
    return {"session": cookie.decode("utf-8")}


@pytest_asyncio.fixture(loop_scope="session")
async def client(session_override) -> AsyncGenerator[AsyncClient, None]:  # noqa: ARG001
    """HTTPX клиент для тестов."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
