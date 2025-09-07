from typing import AsyncGenerator

import pytest
from httpx import AsyncClient, ASGITransport
from itsdangerous import URLSafeSerializer

from dependencies import get_db as get_session
from main import app


@pytest.fixture
async def override_get_session(async_session_maker, event_loop):
    """Переопределяем зависимость get_session на тестовую."""
    async def _get_session():
        async with async_session_maker() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client(test_engine, override_get_session) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX клиент для тестов."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# def make_session_cookie(data: dict, secret_key: str) -> str:
#     # сериализуем как делает Starlette
#     payload = pickle.dumps(data, pickle.HIGHEST_PROTOCOL)
#     base64_payload = base64.b64encode(payload)
#     signer = Signer(secret_key, salt="starlette.sessions")
#     return signer.sign(base64_payload).decode()
#
#
# @pytest.fixture
# async def auth_client(client: AsyncClient, test_user):
#     """HTTPX клиент уже с авторизацией (через SessionMiddleware cookie)."""
#     # cookie_val = make_session_cookie({"user_id": str(test_user.twitch_id)}, "some-secret-key")
#     # client.cookies.set("session", cookie_val)
#     mock_session_data = {"user_id": test_user.twitch_id}
#     mocker.dict("starlette.requests.Request.session", mock_session_data, clear=True)
#     return client


def make_session_cookie(data: dict) -> str:
    s = URLSafeSerializer("some-secret-key", salt="cookie-session")
    return s.dumps(data)

@pytest.fixture
async def auth_client(client: AsyncClient, test_user):
    cookie_value = make_session_cookie({"user_id": test_user.twitch_id})
    client.cookies.set("some-secret-key", cookie_value)
    return client
