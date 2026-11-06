# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты TwitchTokenService (services/twitch_token_service.py).

Часть тестов едет на реальной БД (testcontainers-Postgres, db_session): проверяем
шифрование токенов при сохранении и обнуление при invalid_grant.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import redis.asyncio as aioredis

from database.models import User
from services.twitch_token_service import (
    TwitchTokenExpiredError,
    TwitchTokenRefreshError,
    TwitchTokenService,
)


# No-op переопределение autouse-фикстуры session_override (client_fixtures):
# чистым unit-тестам postgres-контейнер не нужен.
@pytest.fixture
def session_override() -> None:
    return None


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


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr("services.twitch_token_service.httpx.AsyncClient", FakeAsyncClient)
    FakeAsyncClient.response = None
    FakeAsyncClient.last_request = None
    return FakeAsyncClient


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("services.twitch_token_service.asyncio.sleep", AsyncMock())


class FakeLock:
    def __init__(self, result=True, exc=None):
        self.result = result
        self.exc = exc
        self.released = False

    async def acquire(self, blocking=False, blocking_timeout=None):
        if self.exc is not None:
            raise self.exc
        return self.result

    async def release(self):
        self.released = True


class FakeRedisLocks:
    """Фейк Redis, выдающий FakeLock с заданным результатом acquire."""

    def __init__(self, result=True, exc=None):
        self.result = result
        self.exc = exc
        self.lock_names = []

    def lock(self, name, timeout=None):
        self.lock_names.append(name)
        return FakeLock(result=self.result, exc=self.exc)


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

    async def scalar(self, q):
        self.statements.append(q)
        return self.select_result

    async def commit(self):
        self.commits += 1


def make_service(db=None, redis_client=None):
    return TwitchTokenService(db_session_factory=db or (lambda: FakeDb()), redis=redis_client)


def make_user(
    expires_in=3600,
    access_token="acc",
    refresh_token="ref",
    user_id=1,
):
    expires_at = None
    if expires_in is not None:
        expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=expires_in - 1)
    return User(
        id=user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        twitch_token_expires_at=expires_at,
    )


def ok_response(expires_in=3600, refresh_token="new-ref"):
    body = {"access_token": "new-acc", "expires_in": expires_in}
    if refresh_token is not None:
        body["refresh_token"] = refresh_token
    return httpx.Response(200, json=body)


# ── calc_expires_at / _parse_token_error ──────────────────────────────────


def test_calc_expires_at_is_naive_utc():
    before = datetime.now(UTC).replace(tzinfo=None)
    expires_at = TwitchTokenService.calc_expires_at(600)
    after = datetime.now(UTC).replace(tzinfo=None)
    assert expires_at.tzinfo is None
    assert before + timedelta(seconds=599) <= expires_at <= after + timedelta(seconds=599)


def test_parse_token_error_reads_error_fields():
    request = httpx.Request("POST", "https://id.twitch.tv/oauth2/token")
    response = httpx.Response(400, request=request, json={"error": "invalid_grant", "error_description": "boom"})
    assert TwitchTokenService._parse_token_error(response) == ("invalid_grant", "boom")


def test_parse_token_error_non_json_body():
    request = httpx.Request("POST", "https://id.twitch.tv/oauth2/token")
    response = httpx.Response(500, request=request, text="Internal Server Error")
    assert TwitchTokenService._parse_token_error(response) == ("unknown", "Internal Server Error")


def test_parse_token_error_helix_body_invalid_refresh_token_maps_to_invalid_grant():
    """Twitch шлёт протухший refresh-токен Helix-телом без OAuth-полей."""
    request = httpx.Request("POST", "https://id.twitch.tv/oauth2/token")
    response = httpx.Response(400, request=request, json={"status": 400, "message": "Invalid refresh token"})
    assert TwitchTokenService._parse_token_error(response) == ("invalid_grant", "Invalid refresh token")


def test_parse_token_error_helix_body_message_as_description():
    request = httpx.Request("POST", "https://id.twitch.tv/oauth2/token")
    response = httpx.Response(400, request=request, json={"status": 400, "message": "missing client secret"})
    assert TwitchTokenService._parse_token_error(response) == ("unknown", "missing client secret")


# ── _request_refresh ──────────────────────────────────────────────────────


async def test_request_refresh_success_without_new_refresh_token(fake_http):
    fake_http.response = ok_response(refresh_token=None)
    service = make_service()

    result = await service._request_refresh("old-ref")

    assert result["access_token"] == "new-acc"
    # Twitch не прислал новый refresh_token — сохраняем старый
    assert result["refresh_token"] == "old-ref"
    assert result["expires_at"] > datetime.now(UTC).replace(tzinfo=None)
    body = fake_http.last_request["data"]
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "old-ref"


async def test_request_refresh_missing_expires_in(fake_http):
    fake_http.response = httpx.Response(200, json={"access_token": "a"})
    service = make_service()

    with pytest.raises(TwitchTokenRefreshError) as exc_info:
        await service._request_refresh("ref")
    assert exc_info.value.error == "invalid_response"


async def test_request_refresh_invalid_grant(fake_http):
    request = httpx.Request("POST", "https://id.twitch.tv/oauth2/token")
    fake_http.response = httpx.Response(400, request=request, json={"error": "invalid_grant"})
    service = make_service()

    with pytest.raises(TwitchTokenRefreshError) as exc_info:
        await service._request_refresh("ref")
    assert exc_info.value.error == "invalid_grant"
    assert exc_info.value.status_code == 400


async def test_request_refresh_invalid_refresh_token_helix_body(fake_http):
    """Протухший refresh-токен: Helix-тело без OAuth-полей → invalid_grant."""
    request = httpx.Request("POST", "https://id.twitch.tv/oauth2/token")
    fake_http.response = httpx.Response(400, request=request, json={"status": 400, "message": "Invalid refresh token"})
    service = make_service()

    with pytest.raises(TwitchTokenRefreshError) as exc_info:
        await service._request_refresh("dead-ref")
    assert exc_info.value.error == "invalid_grant"
    assert exc_info.value.description == "Invalid refresh token"
    assert exc_info.value.status_code == 400


async def test_request_refresh_5xx_becomes_server_error(fake_http):
    request = httpx.Request("POST", "https://id.twitch.tv/oauth2/token")
    fake_http.response = httpx.Response(503, request=request, text="upstream down")
    service = make_service()

    with pytest.raises(TwitchTokenRefreshError) as exc_info:
        await service._request_refresh("ref")
    assert exc_info.value.error == "server_error"
    assert exc_info.value.status_code == 503
    assert exc_info.value.description == "upstream down"


async def test_request_refresh_5xx_with_invalid_refresh_token_body_stays_server_error(fake_http):
    """5xx с текстом «Invalid refresh token» в теле — всё равно server_error (ретраи), не invalid_grant."""
    request = httpx.Request("POST", "https://id.twitch.tv/oauth2/token")
    fake_http.response = httpx.Response(503, request=request, json={"status": 503, "message": "Invalid refresh token"})
    service = make_service()

    with pytest.raises(TwitchTokenRefreshError) as exc_info:
        await service._request_refresh("ref")
    assert exc_info.value.error == "server_error"
    assert exc_info.value.status_code == 503


# ── get_valid_access_token ────────────────────────────────────────────────


async def test_get_valid_access_token_no_token():
    # property-сеттер User.access_token запрещает None (assert), поэтому
    # сценарий «токенов нет» эмулируем простым объектом.
    user = SimpleNamespace(access_token=None, twitch_token_expires_at=None)
    service = make_service()

    with pytest.raises(TwitchTokenExpiredError) as exc_info:
        await service.get_valid_access_token(user)
    assert exc_info.value.error == "no_access_token"


async def test_get_valid_access_token_fresh_returns_without_refresh(monkeypatch):
    user = make_user(expires_in=3600)
    refresh = AsyncMock()
    service = make_service()
    monkeypatch.setattr(service, "_refresh_tokens_if_need", refresh)

    token = await service.get_valid_access_token(user)

    assert token == "acc"
    refresh.assert_not_awaited()


async def test_get_valid_access_token_expired_triggers_refresh(monkeypatch):
    user = make_user(expires_in=60)
    service = make_service()
    monkeypatch.setattr(
        service, "_refresh_tokens_if_need", AsyncMock(return_value={"access_token": "fresh", "refresh_token": "r"})
    )

    assert await service.get_valid_access_token(user) == "fresh"


async def test_get_valid_access_token_no_expires_triggers_refresh(monkeypatch):
    user = make_user(expires_in=None)
    service = make_service()
    monkeypatch.setattr(
        service, "_refresh_tokens_if_need", AsyncMock(return_value={"access_token": "fresh", "refresh_token": "r"})
    )

    assert await service.get_valid_access_token(user) == "fresh"


# ── _refresh_tokens_if_need ───────────────────────────────────────────────


async def test_refresh_if_need_no_refresh_token():
    user = SimpleNamespace(id=1, access_token="acc", refresh_token=None, twitch_token_expires_at=None)
    service = make_service()

    with pytest.raises(TwitchTokenExpiredError) as exc_info:
        await service._refresh_tokens_if_need(user, block_on_lock=True)
    assert exc_info.value.error == "no_refresh_token"


async def test_refresh_if_need_fresh_token_skipped():
    user = make_user(expires_in=3600)
    db = FakeDb(select_result=user)
    service = make_service(db=lambda: db)

    result = await service._refresh_tokens_if_need(user, block_on_lock=True)

    assert result == {"access_token": "acc", "refresh_token": "ref"}


async def test_refresh_if_need_busy_lock_non_block_returns_current():
    user = make_user(expires_in=60)
    redis = FakeRedisLocks(result=False)
    service = make_service(redis_client=redis)

    result = await service._refresh_tokens_if_need(user, block_on_lock=False)

    assert result == {"access_token": "acc", "refresh_token": "ref"}


async def test_refresh_if_need_reloads_fresh_user_under_lock():
    """Пока ждали лок, другой воркер обновил токен — не рефрешим повторно."""
    user = make_user(expires_in=60, user_id=7)
    refreshed = make_user(expires_in=7200, access_token="other-acc", user_id=7)
    db = FakeDb(select_result=refreshed)
    redis = FakeRedisLocks()
    service = make_service(db=lambda: db, redis_client=redis)

    result = await service._refresh_tokens_if_need(user, block_on_lock=True)

    assert result == {"access_token": "other-acc", "refresh_token": "ref"}
    assert "twitch:refresh:7" in redis.lock_names


async def test_refresh_if_need_reloaded_user_without_refresh_token():
    user = make_user(expires_in=60, user_id=7)
    reloaded = SimpleNamespace(id=7, access_token="acc", refresh_token=None, twitch_token_expires_at=None)
    db = FakeDb(select_result=reloaded)
    service = make_service(db=lambda: db, redis_client=FakeRedisLocks())

    with pytest.raises(TwitchTokenExpiredError) as exc_info:
        await service._refresh_tokens_if_need(user, block_on_lock=True)
    assert exc_info.value.error == "no_refresh_token"


async def test_refresh_if_need_survives_redis_outage_and_refreshes(fake_http, no_sleep):
    """Redis недоступен → лок пропускается, но рефраш всё равно выполняется."""
    user = make_user(expires_in=60)
    db = FakeDb(select_result=None)
    fake_http.response = ok_response()
    redis = FakeRedisLocks(exc=aioredis.ConnectionError("down"))
    service = make_service(db=lambda: db, redis_client=redis)

    result = await service._refresh_tokens_if_need(user, block_on_lock=True)

    assert result["access_token"] == "new-acc"


# ── _refresh_with_retry ───────────────────────────────────────────────────


async def test_refresh_with_retry_invalid_grant_invalidates_tokens(monkeypatch):
    db = FakeDb()
    service = make_service(db=lambda: db)
    request = AsyncMock(side_effect=TwitchTokenRefreshError("invalid_grant", "Invalid refresh token", 400))
    monkeypatch.setattr(service, "_request_refresh", request)

    # TwitchTokenExpiredError (подкласс): роуты показывают по нему UX «переавторизуйтесь»
    with pytest.raises(TwitchTokenExpiredError) as exc_info:
        await service._refresh_with_retry(make_user(), "ref")

    assert exc_info.value.error == "invalid_grant"
    assert exc_info.value.description == "Invalid refresh token"
    # Обнуление токенов выполнено ровно один раз (UPDATE с NULL-параметрами)
    invalidate_stmts = [
        q
        for q in db.statements
        if "UPDATE twitch_bot_users" in str(q)
        and any(v is None for v in q.compile().params.values() if isinstance(v, (str, type(None))))
    ]
    assert len(invalidate_stmts) == 1
    assert request.await_count == 1


async def test_refresh_with_retry_server_error_retries_then_succeeds(no_sleep, monkeypatch):
    service = make_service()
    request = AsyncMock(
        side_effect=[
            TwitchTokenRefreshError("server_error", "5xx", 503),
            TwitchTokenRefreshError("server_error", "5xx", 503),
            {"access_token": "ok-acc", "refresh_token": "ok-ref", "expires_at": datetime.now(UTC)},
        ]
    )
    save = AsyncMock()
    monkeypatch.setattr(service, "_request_refresh", request)
    monkeypatch.setattr(service, "_save_tokens", save)

    result = await service._refresh_with_retry(make_user(), "ref")

    assert result["access_token"] == "ok-acc"
    assert request.await_count == 3
    save.assert_awaited_once()


async def test_refresh_with_retry_network_errors_exhaust_backoffs(no_sleep, monkeypatch):
    service = make_service()
    boom = httpx.ConnectError("no route to host")
    request = AsyncMock(side_effect=boom)
    monkeypatch.setattr(service, "_request_refresh", request)

    with pytest.raises(httpx.ConnectError):
        await service._refresh_with_retry(make_user(), "ref")

    assert request.await_count == 5  # len(_RETRY_BACKOFFS)


async def test_refresh_with_retry_non_retryable_client_error_raises_immediately(monkeypatch):
    service = make_service()
    request = AsyncMock(side_effect=TwitchTokenRefreshError("invalid_client", "bad secret", 401))
    monkeypatch.setattr(service, "_request_refresh", request)

    with pytest.raises(TwitchTokenRefreshError):
        await service._refresh_with_retry(make_user(), "ref")

    assert request.await_count == 1


# ── Реальная БД: сохранение / обнуление / периодическое обновление ────────


class SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


async def test_save_tokens_encrypts_columns_and_getter_decrypts(db_session, test_user):
    service = make_service(db=lambda: SessionCtx(db_session))
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    await service._save_tokens(
        test_user.id, {"access_token": "plain-acc", "refresh_token": "plain-ref", "expires_at": expires_at}
    )
    await db_session.refresh(test_user, attribute_names=["_access_token", "_refresh_token", "twitch_token_expires_at"])

    # В колонках — шифротекст, а property-геттер расшифровывает обратно
    assert test_user._access_token != "plain-acc"
    assert test_user.access_token == "plain-acc"
    assert test_user.refresh_token == "plain-ref"
    assert test_user.twitch_token_expires_at == expires_at


async def test_invalidate_tokens_nulls_columns(db_session, test_user):
    test_user.twitch_token_expires_at = datetime.now(UTC).replace(tzinfo=None)
    await db_session.commit()

    service = make_service(db=lambda: SessionCtx(db_session))
    await service._invalidate_tokens(test_user.id)
    await db_session.refresh(test_user, attribute_names=["_access_token", "_refresh_token", "twitch_token_expires_at"])

    assert test_user._access_token is None
    assert test_user._refresh_token is None
    assert test_user.twitch_token_expires_at is None


async def test_load_user_returns_row(db_session, test_user):
    service = make_service(db=lambda: SessionCtx(db_session))

    loaded = await service._load_user(test_user.id)

    assert loaded is not None
    assert loaded.id == test_user.id


async def test_run_periodic_update_refreshes_expiring_users(db_session, test_user, fake_http, no_sleep):
    # Истекающий пользователь с refresh-токеном: попадает в выборку (<1 дня)
    # и ниже порога рефреша (<600с до истечения)
    test_user.twitch_token_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
    await db_session.commit()

    # Далёкий пользователь — не должен попасть в выборку
    far_user = User(
        twitch_id="777",
        login_name="far_user",
        profile_image_url="https://example.com/far.png",
        access_token="far-acc",
        refresh_token="far-ref",
        twitch_token_expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30),
    )
    db_session.add(far_user)
    await db_session.commit()

    fake_http.response = ok_response()
    service = make_service(db=lambda: SessionCtx(db_session), redis_client=FakeRedisLocks())

    await service.run_periodic_update()

    await db_session.refresh(test_user, attribute_names=["_access_token", "twitch_token_expires_at"])
    assert test_user.access_token == "new-acc"
    assert test_user.twitch_token_expires_at > datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)
    await db_session.refresh(far_user, attribute_names=["_access_token"])
    assert far_user.access_token == "far-acc"
