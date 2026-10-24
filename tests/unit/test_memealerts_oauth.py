# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты MemealertsOAuthService и модели Tokens (services/memes_v2.py)."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import redis.asyncio as aioredis

from database.models import MemealertsSettings, User
from exceptions import (
    MAInvalidScopeError,
    MAInvalidTokenError,
    MANoToken,
    MARefreshTokenError,
    MATokenExpiredError,
    MATokenRefreshError,
)
from services.memes_v2 import MemealertsOAuthService, Tokens


class FakeAsyncClient:
    """Подмена httpx.AsyncClient: отвечает заготовленным ответом/ошибкой."""

    response: httpx.Response | Exception | None = None
    last_request: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        FakeAsyncClient.last_request = {"url": url, **kwargs}
        if isinstance(FakeAsyncClient.response, Exception):
            raise FakeAsyncClient.response
        assert FakeAsyncClient.response is not None
        return FakeAsyncClient.response

    async def get(self, url, **kwargs):
        return await self.post(url, **kwargs)


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr("services.memes_v2.httpx.AsyncClient", FakeAsyncClient)
    FakeAsyncClient.response = None
    FakeAsyncClient.last_request = None
    return FakeAsyncClient


class FakeDb:
    """Минимальная сессия БД: scalar-результат + журнал execute/commit."""

    def __init__(self, select_result=None):
        self.select_result = select_result
        self.statements = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, q):
        self.statements.append(q)
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: self.select_result or []),
            scalar_one_or_none=lambda: self.select_result,
        )

    async def commit(self):
        self.commits += 1


class FakeStatistics:
    def __init__(self):
        self.calls = []

    def inc(self, type_, subtype=""):
        self.calls.append(subtype or str(type_))


def make_oauth_service(db=None, statistics=None, redis_client=None):
    return MemealertsOAuthService(
        db_session_factory=db or (lambda: FakeDb()),
        redis=redis_client,
        statistics=statistics,
    )


def make_ma_settings(
    access_token="acc",
    refresh_token="ref",
    scopes="oauth-user-show oauth-bonus-give",
    expires_in=3600,
):
    now = datetime.now(UTC).replace(tzinfo=None)
    return MemealertsSettings(
        user_id=1,
        access_token=access_token,
        refresh_token=refresh_token,
        token_scopes=scopes,
        token_expires_at=now + timedelta(seconds=expires_in - 1),
        token_refresh_expires_at=now + timedelta(days=25),
    )


# ── Tokens ──────────────────────────────────────────────────────────────────


def test_tokens_from_expires_in():
    tokens = Tokens(access_token="a", refresh_token="r", expires_in=3600)

    assert tokens.expires_in == pytest.approx(3599, abs=5)
    assert tokens.is_expired is False
    assert tokens.refresh_expires_in > 29 * 24 * 3600
    assert tokens.is_refresh_expired is False


def test_tokens_from_expires_at():
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    tokens = Tokens(access_token="a", refresh_token="r", expires_at=expires_at)

    assert tokens.expires_at == expires_at
    assert tokens.refresh_expires_at == expires_at + timedelta(days=30, hours=-1)
    assert tokens.is_expired is False


def test_tokens_expired():
    expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31)

    tokens = Tokens(access_token="a", refresh_token="r", expires_at=expires_at)

    assert tokens.is_expired is True
    assert tokens.is_refresh_expired is True
    assert tokens.expires_in == 0


def test_tokens_require_expiration():
    with pytest.raises(ValueError, match="expires_in"):
        Tokens(access_token="a", refresh_token="r")


# ── _request_tokens / _parse_token_error ───────────────────────────────────


async def test_request_tokens_argument_validation():
    service = make_oauth_service()
    with pytest.raises(ValueError, match="Only one"):
        await service._request_tokens(authorization_code="c", refresh_token="r")
    with pytest.raises(ValueError, match="One of"):
        await service._request_tokens()


async def test_request_tokens_success(fake_http):
    fake_http.response = httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600})
    service = make_oauth_service()

    tokens = await service._request_tokens(refresh_token="old")

    assert tokens.access_token == "a"
    assert fake_http.last_request["data"]["grant_type"] == "refresh_token"
    assert fake_http.last_request["data"]["refresh_token"] == "old"
    assert fake_http.last_request["data"]["client_id"]  # секреты подставляются


async def test_request_tokens_authorization_code_grant(fake_http):
    fake_http.response = httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600})
    service = make_oauth_service()

    await service._request_tokens(authorization_code="code-1")

    assert fake_http.last_request["data"]["grant_type"] == "authorization_code"
    assert fake_http.last_request["data"]["code"] == "code-1"
    assert fake_http.last_request["data"]["redirect_uri"]


async def test_request_tokens_error_payload(fake_http):
    fake_http.response = httpx.Response(400, json={"error": "invalid_grant", "error_description": "bad code"})
    service = make_oauth_service()

    with pytest.raises(MATokenRefreshError) as exc_info:
        await service._request_tokens(refresh_token="old")

    assert exc_info.value.error == "invalid_grant"
    assert exc_info.value.description == "bad code"


async def test_request_tokens_5xx_forced_server_error(fake_http):
    fake_http.response = httpx.Response(503, text="Service Unavailable")
    service = make_oauth_service()

    with pytest.raises(MATokenRefreshError) as exc_info:
        await service._request_tokens(refresh_token="old")

    assert exc_info.value.error == "server_error"
    assert "Service Unavailable" in exc_info.value.description


async def test_request_tokens_invalid_body(fake_http):
    fake_http.response = httpx.Response(200, json={"unexpected": True})
    service = make_oauth_service()

    with pytest.raises(MARefreshTokenError):
        await service._request_tokens(refresh_token="old")


def test_parse_token_error_variants():
    ok = httpx.Response(400, json={"error": "e", "error_description": "d"})
    assert MemealertsOAuthService._parse_token_error(ok) == ("e", "d")

    no_json = httpx.Response(400, text="html")
    assert MemealertsOAuthService._parse_token_error(no_json) == ("unknown", "html")

    not_dict = httpx.Response(400, json=["x"])
    assert MemealertsOAuthService._parse_token_error(not_dict) == ("unknown", "['x']")

    no_error = httpx.Response(400, json={})
    assert MemealertsOAuthService._parse_token_error(no_error) == ("unknown", "")


# ── _refresh_tokens_if_need ─────────────────────────────────────────────────


async def test_refresh_if_need_skips_fresh_tokens():
    """Токен живёт > 10 минут — обновление не нужно, БД не трогаем."""
    service = make_oauth_service(db=None)
    tokens = Tokens(access_token="a", refresh_token="r", expires_in=3600)

    result = await service._refresh_tokens_if_need(tokens, user_id=1)

    assert result is tokens


async def test_refresh_if_need_refresh_expired():
    statistics = FakeStatistics()
    service = make_oauth_service(statistics=statistics)
    naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31)
    tokens = Tokens(access_token="a", refresh_token="r", expires_at=naive_past)

    with pytest.raises(MATokenExpiredError):
        await service._refresh_tokens_if_need(tokens, user_id=1)

    assert "expired" in statistics.calls


async def test_refresh_if_need_returns_fresh_db_token(monkeypatch):
    """Под локом токен перечитан из БД и уже обновлён другим воркером."""
    statistics = FakeStatistics()
    service = make_oauth_service(statistics=statistics)
    fresh = Tokens(access_token="new", refresh_token="r2", expires_in=3600)
    monkeypatch.setattr(service, "_load_tokens_from_db", AsyncMock(return_value=fresh))

    result = await service._refresh_tokens_if_need(
        Tokens(access_token="old", refresh_token="r", expires_in=60), user_id=1
    )

    assert result is fresh
    assert "success" in statistics.calls


async def test_refresh_if_need_fresh_db_token_expired(monkeypatch):
    service = make_oauth_service()
    naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31)
    expired = Tokens(access_token="old", refresh_token="r", expires_at=naive_past)
    monkeypatch.setattr(service, "_load_tokens_from_db", AsyncMock(return_value=expired))

    with pytest.raises(MATokenExpiredError):
        await service._refresh_tokens_if_need(Tokens(access_token="old", refresh_token="r", expires_in=60), user_id=1)


async def test_refresh_if_need_routes_to_retry_path(monkeypatch):
    """expires_in <= 60 — обновление с ретраями."""
    service = make_oauth_service()
    expected = Tokens(access_token="new", refresh_token="r2", expires_in=3600)
    monkeypatch.setattr(service, "_load_tokens_from_db", AsyncMock(return_value=None))
    retry = AsyncMock(return_value=expected)
    monkeypatch.setattr(service, "_refresh_with_retry", retry)

    result = await service._refresh_tokens_if_need(
        Tokens(access_token="a", refresh_token="r", expires_in=30), user_id=1
    )

    assert result is expected
    assert retry.await_count == 1


async def test_refresh_if_need_routes_to_keep_old_path(monkeypatch):
    """60 < expires_in <= 600 — мягкое обновление."""
    service = make_oauth_service()
    expected = Tokens(access_token="new", refresh_token="r2", expires_in=3600)
    monkeypatch.setattr(service, "_load_tokens_from_db", AsyncMock(return_value=None))
    keep_old = AsyncMock(return_value=expected)
    monkeypatch.setattr(service, "_refresh_or_keep_old", keep_old)

    result = await service._refresh_tokens_if_need(
        Tokens(access_token="a", refresh_token="r", expires_in=300), user_id=1
    )

    assert result is expected
    assert keep_old.await_count == 1


async def test_refresh_if_need_busy_lock_skipped(monkeypatch):
    """Фоновая задача (block=False): лок занят — пропускаем обновление."""
    service = make_oauth_service()
    tokens = Tokens(access_token="a", refresh_token="r", expires_in=30)

    @asynccontextmanager
    async def fake_lock(user_id, *, block):
        assert block is False
        yield False

    monkeypatch.setattr(service, "_acquire_refresh_lock", fake_lock)
    load = AsyncMock()
    monkeypatch.setattr(service, "_load_tokens_from_db", load)

    result = await service._refresh_tokens_if_need(tokens, user_id=1, block_on_lock=False)

    assert result is tokens
    load.assert_not_awaited()


async def test_refresh_if_need_survives_redis_outage(monkeypatch):
    """Redis лёг — лок не получен, но блокирующий runtime-запрос всё равно обновляет."""
    service = make_oauth_service(
        redis_client=SimpleNamespace(
            lock=lambda *a, **kw: SimpleNamespace(
                acquire=AsyncMock(side_effect=aioredis.ConnectionError("down")),
                release=AsyncMock(),
            )
        )
    )
    expected = Tokens(access_token="new", refresh_token="r2", expires_in=3600)
    monkeypatch.setattr(service, "_load_tokens_from_db", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_refresh_with_retry", AsyncMock(return_value=expected))

    result = await service._refresh_tokens_if_need(
        Tokens(access_token="a", refresh_token="r", expires_in=30), user_id=1
    )

    assert result is expected


# ── _refresh_with_retry / _refresh_or_keep_old ──────────────────────────────


async def test_refresh_with_retry_success_first_attempt(fake_http):
    fake_http.response = httpx.Response(200, json={"access_token": "a2", "refresh_token": "r2", "expires_in": 3600})
    statistics = FakeStatistics()
    service = make_oauth_service(statistics=statistics)

    tokens = await service._refresh_with_retry(Tokens(access_token="a", refresh_token="r", expires_in=30), user_id=1)

    assert tokens.access_token == "a2"
    assert "success" in statistics.calls


async def test_refresh_with_retry_non_retryable_raised(fake_http):
    fake_http.response = httpx.Response(400, json={"error": "invalid_grant"})
    statistics = FakeStatistics()
    service = make_oauth_service(statistics=statistics)

    with pytest.raises(MATokenRefreshError) as exc_info:
        await service._refresh_with_retry(Tokens(access_token="a", refresh_token="r", expires_in=30), user_id=1)

    assert exc_info.value.error == "invalid_grant"
    assert "invalid_grant" in statistics.calls


async def test_refresh_with_retry_exhausts_backoffs(fake_http, monkeypatch):
    fake_http.response = httpx.Response(400, json={"error": "server_error"})
    monkeypatch.setattr("services.memes_v2.asyncio.sleep", AsyncMock())
    statistics = FakeStatistics()
    service = make_oauth_service(statistics=statistics)

    with pytest.raises(MATokenRefreshError):
        await service._refresh_with_retry(Tokens(access_token="a", refresh_token="r", expires_in=30), user_id=1)

    # 5 бэкоффей = 5 попыток, ни одной реальной паузы
    assert fake_http.last_request is not None
    assert len(MemealertsOAuthService._RETRY_BACKOFFS) == 5
    assert "server_error" in statistics.calls


async def test_refresh_with_retry_network_error(fake_http, monkeypatch):
    fake_http.response = httpx.ConnectError("boom")
    monkeypatch.setattr("services.memes_v2.asyncio.sleep", AsyncMock())
    statistics = FakeStatistics()
    service = make_oauth_service(statistics=statistics)

    with pytest.raises(httpx.ConnectError):
        await service._refresh_with_retry(Tokens(access_token="a", refresh_token="r", expires_in=30), user_id=1)

    assert "network_error" in statistics.calls


async def test_refresh_or_keep_old_success(fake_http):
    fake_http.response = httpx.Response(200, json={"access_token": "a2", "refresh_token": "r2", "expires_in": 3600})
    service = make_oauth_service()

    tokens = await service._refresh_or_keep_old(Tokens(access_token="a", refresh_token="r", expires_in=300), user_id=1)

    assert tokens.access_token == "a2"


async def test_refresh_or_keep_old_server_error_keeps_old(fake_http):
    fake_http.response = httpx.Response(400, json={"error": "server_error"})
    statistics = FakeStatistics()
    service = make_oauth_service(statistics=statistics)
    old = Tokens(access_token="a", refresh_token="r", expires_in=300)

    result = await service._refresh_or_keep_old(old, user_id=1)

    assert result is old
    assert "server_error" in statistics.calls


async def test_refresh_or_keep_old_network_error_keeps_old(fake_http):
    fake_http.response = httpx.ReadTimeout("slow")
    statistics = FakeStatistics()
    service = make_oauth_service(statistics=statistics)
    old = Tokens(access_token="a", refresh_token="r", expires_in=300)

    result = await service._refresh_or_keep_old(old, user_id=1)

    assert result is old
    assert "network_error" in statistics.calls


async def test_refresh_or_keep_old_fatal_error_raises(fake_http):
    fake_http.response = httpx.Response(400, json={"error": "invalid_client"})
    service = make_oauth_service()
    old = Tokens(access_token="a", refresh_token="r", expires_in=300)

    with pytest.raises(MATokenRefreshError):
        await service._refresh_or_keep_old(old, user_id=1)


# ── get_token_of_user ───────────────────────────────────────────────────────


def make_user(settings=None):
    """Настоящая (transient) модель User: get_token_of_user проверяет isinstance."""
    user = User(twitch_id="7", login_name="streamer")
    user.id = 7
    user.memealerts = settings or make_ma_settings()
    return user


async def test_get_token_of_user_no_token():
    service = make_oauth_service()
    user = make_user(make_ma_settings(access_token=None))

    with pytest.raises(MANoToken):
        await service.get_token_of_user(user)


async def test_get_token_of_user_invalid_scope():
    service = make_oauth_service()
    user = make_user(make_ma_settings(scopes="oauth-user-show"))

    with pytest.raises(MAInvalidScopeError):
        await service.get_token_of_user(user)


async def test_get_token_of_user_revoked_tokens_deleted(monkeypatch):
    """invalid_grant в рантайме: токены отозваны — чистим БД, отдаём MAInvalidTokenError."""
    service = make_oauth_service()
    user = make_user()
    monkeypatch.setattr(
        service,
        "_refresh_tokens_if_need",
        AsyncMock(side_effect=MATokenRefreshError(error="invalid_grant", description="revoked")),
    )
    delete = AsyncMock()
    monkeypatch.setattr(service, "delete_token_by_id", delete)

    with pytest.raises(MAInvalidTokenError):
        await service.get_token_of_user(user)

    delete.assert_awaited_once_with(7)


async def test_get_token_of_user_invalid_client_propagates(monkeypatch):
    service = make_oauth_service()
    user = make_user()
    monkeypatch.setattr(
        service,
        "_refresh_tokens_if_need",
        AsyncMock(side_effect=MATokenRefreshError(error="invalid_client", description="cfg")),
    )
    delete = AsyncMock()
    monkeypatch.setattr(service, "delete_token_by_id", delete)

    with pytest.raises(MATokenRefreshError):
        await service.get_token_of_user(user)

    delete.assert_not_awaited()


async def test_get_token_of_user_refresh_expired_deleted(monkeypatch):
    service = make_oauth_service()
    user = make_user()
    monkeypatch.setattr(service, "_refresh_tokens_if_need", AsyncMock(side_effect=MATokenExpiredError))
    delete = AsyncMock()
    monkeypatch.setattr(service, "delete_token_by_id", delete)

    with pytest.raises(MAInvalidTokenError):
        await service.get_token_of_user(user)

    delete.assert_awaited_once_with(7)


async def test_get_token_of_user_saves_refreshed(monkeypatch):
    service = make_oauth_service()
    user = make_user()
    new = Tokens(access_token="a2", refresh_token="r2", expires_in=3600)
    monkeypatch.setattr(service, "_refresh_tokens_if_need", AsyncMock(return_value=new))
    save = AsyncMock()
    monkeypatch.setattr(service, "_save_token", save)

    result = await service.get_token_of_user(user)

    assert result is new
    save.assert_awaited_once_with(new, 7)


async def test_get_token_of_user_unchanged_token_not_saved(monkeypatch):
    service = make_oauth_service()
    user = make_user()
    current = MemealertsOAuthService._tokens_from_settings(user.memealerts)
    monkeypatch.setattr(service, "_refresh_tokens_if_need", AsyncMock(return_value=current))
    save = AsyncMock()
    monkeypatch.setattr(service, "_save_token", save)

    result = await service.get_token_of_user(user)

    assert result is current
    save.assert_not_awaited()


# ── auth_user / сохранение / периодическое обновление ──────────────────────


async def test_auth_user_saves_tokens(monkeypatch):
    service = make_oauth_service()
    tokens = Tokens(access_token="a", refresh_token="r", expires_in=3600)
    monkeypatch.setattr(service, "_request_tokens", AsyncMock(return_value=tokens))
    save = AsyncMock()
    monkeypatch.setattr(service, "_save_token", save)

    result = await service.auth_user("auth-code", make_user())

    assert result is tokens
    save.assert_awaited_once_with(tokens=tokens, user_id=7)


async def test_auth_user_none_tokens_not_saved(monkeypatch):
    service = make_oauth_service()
    monkeypatch.setattr(service, "_request_tokens", AsyncMock(return_value=None))
    save = AsyncMock()
    monkeypatch.setattr(service, "_save_token", save)

    assert await service.auth_user("auth-code", make_user()) is None
    save.assert_not_awaited()


async def test_save_token_with_session_executes_update():
    db = FakeDb()
    service = make_oauth_service()
    tokens = Tokens(access_token="a", refresh_token="r", expires_in=3600)

    await service._save_token(tokens, user_id=1, session=db)

    assert len(db.statements) == 1
    assert db.commits == 0  # коммитит вызывающий


async def test_save_token_without_session_commits():
    db = FakeDb()
    service = make_oauth_service(db=lambda: db)
    tokens = Tokens(access_token="a", refresh_token="r", expires_in=3600)

    await service._save_token(tokens, user_id=1)

    assert len(db.statements) == 1
    assert db.commits == 1


async def test_delete_token_by_id():
    db = FakeDb()
    service = make_oauth_service(db=lambda: db)

    await service.delete_token_by_id(42)

    assert len(db.statements) == 1
    assert db.commits == 1


async def test_run_periodic_update_saves_changed_tokens(monkeypatch):
    db = FakeDb(select_result=[make_ma_settings()])
    service = make_oauth_service(db=lambda: db)
    new = Tokens(access_token="a2", refresh_token="r2", expires_in=3600)
    monkeypatch.setattr(service, "_refresh_tokens_if_need", AsyncMock(return_value=new))
    save = AsyncMock()
    monkeypatch.setattr(service, "_save_token", save)

    await service.run_periodic_update()

    save.assert_awaited_once()
    assert save.await_args.args[0] is new
    assert db.commits == 1  # батч-коммит после цикла


async def test_run_periodic_update_skips_unchanged_tokens(monkeypatch):
    ma_settings = make_ma_settings()
    db = FakeDb(select_result=[ma_settings])
    service = make_oauth_service(db=lambda: db)
    # Тот же объект настроек → равные Tokens → «не обновлён, т.к. не был изменён»
    same = MemealertsOAuthService._tokens_from_settings(ma_settings)
    monkeypatch.setattr(service, "_refresh_tokens_if_need", AsyncMock(return_value=same))
    save = AsyncMock()
    monkeypatch.setattr(service, "_save_token", save)

    await service.run_periodic_update()

    save.assert_not_awaited()
    assert db.commits == 0


async def test_run_periodic_update_handles_refresh_error(monkeypatch):
    """invalid_grant из фоновой задачи: _handle_refresh_error чистит токены."""
    db = FakeDb(select_result=[make_ma_settings()])
    service = make_oauth_service(db=lambda: db)
    monkeypatch.setattr(
        service,
        "_refresh_tokens_if_need",
        AsyncMock(side_effect=MATokenRefreshError(error="invalid_grant", description="revoked")),
    )
    delete = AsyncMock()
    monkeypatch.setattr(service, "delete_token_by_id", delete)

    await service.run_periodic_update()

    delete.assert_awaited_once_with(1)


# ── _handle_refresh_error ──────────────────────────────────────────────────


async def test_handle_refresh_error_invalid_grant_deletes(monkeypatch):
    service = make_oauth_service()
    delete = AsyncMock()
    monkeypatch.setattr(service, "delete_token_by_id", delete)

    await service._handle_refresh_error(1, MATokenRefreshError(error="invalid_grant", description="d"))

    delete.assert_awaited_once_with(1)


async def test_handle_refresh_error_invalid_client_keeps_token(monkeypatch):
    service = make_oauth_service()
    delete = AsyncMock()
    monkeypatch.setattr(service, "delete_token_by_id", delete)

    await service._handle_refresh_error(1, MATokenRefreshError(error="invalid_client", description="d"))

    delete.assert_not_awaited()


async def test_handle_refresh_error_network_keeps_token(monkeypatch):
    service = make_oauth_service()
    delete = AsyncMock()
    monkeypatch.setattr(service, "delete_token_by_id", delete)

    await service._handle_refresh_error(1, httpx.ConnectError("net"))

    delete.assert_not_awaited()


async def test_handle_refresh_error_expired_deletes(monkeypatch):
    service = make_oauth_service()
    delete = AsyncMock()
    monkeypatch.setattr(service, "delete_token_by_id", delete)

    await service._handle_refresh_error(1, MATokenExpiredError())

    delete.assert_awaited_once_with(1)


# ── _load_tokens_from_db ───────────────────────────────────────────────────


async def test_load_tokens_from_db():
    db = FakeDb(select_result=make_ma_settings())
    service = make_oauth_service(db=lambda: db)

    tokens = await service._load_tokens_from_db(1)

    assert tokens is not None
    assert tokens.access_token == "acc"


async def test_load_tokens_from_db_missing_row():
    db = FakeDb(select_result=None)
    service = make_oauth_service(db=lambda: db)

    assert await service._load_tokens_from_db(1) is None
