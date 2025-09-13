import json
from base64 import b64encode
from typing import AsyncGenerator

import itsdangerous
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db
from main import app
from routers.security_helpers import user_auth, user_auth_optional


@pytest.fixture(scope="function", autouse=True)
def session_override(db_session):

    async def get_session_override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = get_session_override


@pytest.fixture(scope="function")
def user_auth_mock(db_session, test_user):
    app.dependency_overrides[user_auth] = lambda : test_user
    app.dependency_overrides[user_auth_optional] = lambda : test_user


@pytest.fixture(scope="function")
def test_user_cookie(test_user) -> dict[str, str]:
    k = app.user_middleware[0].kwargs['secret_key']
    signer = itsdangerous.TimestampSigner(str(k))
    cookie = signer.sign(b64encode(json.dumps({"user_id": test_user.twitch_id}).encode('utf-8')))
    return {"session": cookie.decode('utf-8')}


@pytest.fixture(scope="function")
async def client(session_override) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX клиент для тестов."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
