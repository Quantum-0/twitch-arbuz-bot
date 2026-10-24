# ruff: noqa: S101, ARG001, ARG002

from base64 import b64encode

import pytest

from config import settings


@pytest.mark.asyncio
async def test_openapi(client):
    resp = await client.get("/docs")
    assert resp.status_code == 200
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unauthorized_index_page(client):
    """Неавторизованный `/` — лендинг с согласиями (ФЗ-152), без редиректа."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert 'id="twitchLoginBtn"' in resp.text


@pytest.mark.asyncio
async def test_authorized_index_page(client, test_user_cookie):
    resp = await client.get("/", cookies=test_user_cookie)
    assert resp.status_code == 307
    assert resp.next_request.url.path == "/panel"


@pytest.mark.asyncio
async def test_admin_api_not_authorized(client):
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_api_cookie(client, test_user_cookie):
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test", cookies=test_user_cookie)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_api_invalid_creds(client):
    encoded_credentials = b64encode(b"random_user:random_password").decode("utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_api_valid_creds(client):
    encoded_credentials = b64encode(
        f"{settings.app.admin_api_login}:{settings.app.admin_api_password.get_secret_value()}".encode()
    ).decode("utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test", headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_api_valid_creds_and_cookie(client, test_user_cookie):
    encoded_credentials = b64encode(
        f"{settings.app.admin_api_login}:{settings.app.admin_api_password.get_secret_value()}".encode()
    ).decode("utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.post(
        "/api/admin/add_to_beta_test?twitch_login=test",
        headers=headers,
        cookies=test_user_cookie,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="session")
async def test_get_streamers(client, test_user):
    """/streamers — заглушка: список грузится через /api/user/streamers (streamers.js)."""
    resp = await client.get("/streamers")
    assert resp.status_code == 200
    assert "/static/js/streamers.js" in resp.text


@pytest.mark.asyncio
async def test_debug_forbidden_for_regular_user(client, user_auth_mock):
    resp = await client.get("/debug")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_debug_ok_for_beta_tester(client, user_auth_mock, test_user):
    """/debug для бета-тестера: Provide-зависимости подменяем через override,
    ключ берём из сигнатуры роута (каждый Provide[...] — уникальный объект)."""
    import inspect

    from main import app
    from routers.web.pages import debug_page

    test_user.in_beta_test = True

    class _FakeStateManager:
        async def get_all_from_channel(self, *, channel=None):
            return
            yield  # noqa — маркер асинхронного генератора без значений

    class _FakeChatBot:
        async def get_last_active_users(self, user):
            return []

    sig = inspect.signature(debug_page)
    fakes = {"chat_bot": _FakeChatBot(), "state_manager": _FakeStateManager()}
    override_keys = []
    for param_name, fake in fakes.items():
        dep = sig.parameters[param_name].annotation.__metadata__[0].dependency
        app.dependency_overrides[dep] = lambda f=fake: f
        override_keys.append(dep)
    try:
        resp = await client.get("/debug")
    finally:
        for dep in override_keys:
            app.dependency_overrides.pop(dep, None)

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_panel(client, user_auth_mock):
    resp = await client.get("/panel")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_todo(client, user_auth_mock):
    resp = await client.get("/kinda_roadmap")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_memealerts_tutorial(client, user_auth_mock):
    resp = await client.get("/memealerts-tutorial")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_about(client, user_auth_mock):
    resp = await client.get("/about")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_frontend_no_auth(client, user_auth_mock):
    resp = await client.get("/admin")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_frontend_invalid_user(client, user_auth_mock):
    encoded_credentials = b64encode(
        f"{settings.app.admin_api_login}:{settings.app.admin_api_password.get_secret_value()}".encode()
    ).decode("utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.get("/admin", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_frontend_valid_user(client, user_auth_mock, test_user):
    test_user.login_name = "quantum075"
    encoded_credentials = b64encode(
        f"{settings.app.admin_api_login}:{settings.app.admin_api_password.get_secret_value()}".encode()
    ).decode("utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.get("/admin", headers=headers)
    assert resp.status_code == 200
