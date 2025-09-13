from base64 import b64encode

import pytest

from config import settings


@pytest.mark.asyncio()
async def test_openapi(client):
    resp = await client.get("/docs")
    assert resp.status_code == 200
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200


@pytest.mark.asyncio()
async def test_unauthorized_index_page(client):
    resp = await client.get("/")
    assert resp.status_code == 307
    assert resp.next_request.url.path == "/login"


@pytest.mark.asyncio()
async def test_authorized_index_page(client, test_user_cookie):
    resp = await client.get("/", cookies=test_user_cookie)
    assert resp.status_code == 307
    assert resp.next_request.url.path == "/panel"


@pytest.mark.asyncio()
async def test_admin_api_not_authorized(client):
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test")
    assert resp.status_code == 401

@pytest.mark.asyncio()
async def test_admin_api_cookie(client, test_user_cookie):
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test", cookies=test_user_cookie)
    assert resp.status_code == 401

@pytest.mark.asyncio()
async def test_admin_api_invalid_creds(client):
    encoded_credentials = b64encode(f"random_user:random_password".encode("utf-8")).decode("utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test", headers=headers)
    assert resp.status_code == 403

@pytest.mark.asyncio()
async def test_admin_api_valid_creds(client):
    encoded_credentials = b64encode(f"{settings.admin_api_login}:{settings.admin_api_password}".encode("utf-8")).decode("utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test", headers=headers)
    assert resp.status_code == 401

@pytest.mark.asyncio()
async def test_admin_api_valid_creds_and_cookie(client, test_user_cookie):
    encoded_credentials = b64encode(f"{settings.admin_api_login}:{settings.admin_api_password}".encode("utf-8")).decode("utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test", headers=headers, cookies=test_user_cookie)
    assert resp.status_code == 200

@pytest.mark.asyncio(loop_scope="session")
async def test_get_streamers(client, test_user):
    resp = await client.get("/streamers")
    assert resp.status_code == 200
    assert """<a href="https://twitch.tv/test_user" target="_blank">
                <img src="https://example.com/avatar.png" alt="test_user" class="streamer-avatar">
            </a>""" in resp.text

@pytest.mark.asyncio()
async def test_panel(client, user_auth_mock):
    resp = await client.get("/panel")
    assert resp.status_code == 200

@pytest.mark.asyncio()
async def test_debug(client, user_auth_mock):
    resp = await client.get("/debug")
    assert resp.status_code == 200

@pytest.mark.asyncio()
async def test_todo(client, user_auth_mock):
    resp = await client.get("/kinda_roadmap")
    assert resp.status_code == 200

@pytest.mark.asyncio()
async def test_memealerts_tutorial(client, user_auth_mock):
    resp = await client.get("/memealerts-tutorial")
    assert resp.status_code == 200

@pytest.mark.asyncio()
async def test_about(client, user_auth_mock):
    resp = await client.get("/about")
    assert resp.status_code == 200

@pytest.mark.asyncio()
async def test_admin_frontend_no_auth(client, user_auth_mock):
    resp = await client.get("/admin")
    assert resp.status_code == 401

@pytest.mark.asyncio()
async def test_admin_frontend_invalid_user(client, user_auth_mock):
    encoded_credentials = b64encode(f"{settings.admin_api_login}:{settings.admin_api_password}".encode("utf-8")).decode(
        "utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.get("/admin", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio()
async def test_admin_frontend_valid_user(client, user_auth_mock, test_user):
    test_user.login_name = "quantum075"
    encoded_credentials = b64encode(f"{settings.admin_api_login}:{settings.admin_api_password}".encode("utf-8")).decode(
        "utf-8")
    authorization_header = f"Basic {encoded_credentials}"
    headers = {"Authorization": authorization_header}
    resp = await client.get("/admin", headers=headers)
    assert resp.status_code == 200
