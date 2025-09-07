import pytest


@pytest.mark.asyncio
async def test_openapi(client):
    resp = await client.get("/docs")
    assert resp.status_code == 200
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unauthorized_index_page(client):
    resp = await client.get("/")
    assert resp.status_code == 307
    assert resp.next_request.url.path == "/login"


@pytest.mark.asyncio
async def test_authorized_index_page(auth_client):
    resp = await auth_client.get("/")
    assert resp.status_code == 307
    assert resp.next_request.url.path == "/panel"


@pytest.mark.asyncio
async def test_admin_api_not_authorized(client):
    resp = await client.post("/api/admin/add_to_beta_test?twitch_login=test")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_streamers(client):
    resp = await client.get("/streamers")
    assert resp.status_code == 200
