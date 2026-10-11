"""Юнит-тесты Telegram Login (OIDC): PKCE, auth URL, релей-обмен code, валидация id_token по JWKS."""

import base64
import hashlib
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from config import settings
from services.telegram_oidc import TelegramLoginService

CLIENT_ID = 123456789
CLIENT_SECRET = "test-secret"  # noqa: S105


def make_login() -> TelegramLoginService:
    return TelegramLoginService()


def configure(monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_login_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "telegram_login_client_secret", SecretStr(CLIENT_SECRET))


def gen_rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_id_token(
    private_key,
    *,
    aud: str = str(CLIENT_ID),
    iss: str = "https://oauth.telegram.org",
    expires_in: int = 3600,
    tg_id: int = 987654321,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": iss,
        "aud": aud,
        "sub": "1234123412341234123",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "id": tg_id,
        "name": "John Doe",
        "preferred_username": "johndoe",
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


class StaticJWKClient:
    """Стаб PyJWKClient с заранее известным ключом (без сетевых запросов)."""

    def __init__(self, jwk: dict):
        self._key = jwt.PyJWK(jwk, algorithm="RS256")

    def get_signing_key_from_jwt(self, _token: str):
        return self._key


def patch_jwks(monkeypatch, private_key) -> None:
    public_jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    monkeypatch.setattr("services.telegram_oidc._jwk_client", StaticJWKClient(public_jwk))


class FakeResponse:
    """Стаб httpx.Response для релей-обмена code."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://relay.test/api/oauth/token")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("relay error", request=request, response=response)

    def json(self):
        return self._json


class FakeAsyncClient:
    """Стаб httpx.AsyncClient: запоминает запрос, возвращает заданный ответ."""

    next_response: FakeResponse | None = None
    captured: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        FakeAsyncClient.captured = {"url": url, **kwargs}
        assert FakeAsyncClient.next_response is not None
        return FakeAsyncClient.next_response


def patch_relay(monkeypatch, response: FakeResponse) -> None:
    FakeAsyncClient.next_response = response
    FakeAsyncClient.captured = {}
    monkeypatch.setattr("services.telegram_oidc.httpx.AsyncClient", FakeAsyncClient)


def test_is_configured(monkeypatch) -> None:
    configure(monkeypatch)
    assert make_login().is_configured is True


def test_not_configured_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_login_client_id", None)
    monkeypatch.setattr(settings, "telegram_login_client_secret", SecretStr(""))
    assert make_login().is_configured is False


def test_pkce_challenge_is_s256_base64url() -> None:
    service = make_login()
    verifier = service.generate_pkce_verifier()
    assert len(verifier) >= 43
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert service.pkce_challenge(verifier) == expected


def test_build_auth_url(monkeypatch) -> None:
    from urllib.parse import parse_qs, urlparse

    configure(monkeypatch)
    url = make_login().build_auth_url("state123", "challenge456")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "oauth.telegram.org"
    assert parsed.path == "/auth"
    q = parse_qs(parsed.query)
    assert q["client_id"] == [str(CLIENT_ID)]
    assert q["response_type"] == ["code"]
    assert q["scope"] == ["openid profile telegram:bot_access"]
    assert q["state"] == ["state123"]
    assert q["code_challenge"] == ["challenge456"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == [str(settings.telegram_login_redirect_url)]


async def test_validate_id_token_ok(monkeypatch) -> None:
    configure(monkeypatch)
    key = gen_rsa_key()
    patch_jwks(monkeypatch, key)
    token = make_id_token(key)

    claims = await make_login().validate_id_token(token)

    assert claims is not None
    assert claims["id"] == 987654321
    assert claims["preferred_username"] == "johndoe"


async def test_validate_id_token_rejects_wrong_audience(monkeypatch) -> None:
    configure(monkeypatch)
    key = gen_rsa_key()
    patch_jwks(monkeypatch, key)
    token = make_id_token(key, aud="999999999")

    assert await make_login().validate_id_token(token) is None


async def test_validate_id_token_rejects_expired(monkeypatch) -> None:
    configure(monkeypatch)
    key = gen_rsa_key()
    patch_jwks(monkeypatch, key)
    token = make_id_token(key, expires_in=-60)

    assert await make_login().validate_id_token(token) is None


async def test_validate_id_token_rejects_wrong_issuer(monkeypatch) -> None:
    configure(monkeypatch)
    key = gen_rsa_key()
    patch_jwks(monkeypatch, key)
    token = make_id_token(key, iss="https://evil.example.com")

    assert await make_login().validate_id_token(token) is None


async def test_validate_id_token_rejects_foreign_signature(monkeypatch) -> None:
    configure(monkeypatch)
    signing_key = gen_rsa_key()
    other_key = gen_rsa_key()
    # JWKS содержит чужой публичный ключ → подпись не сходится.
    patch_jwks(monkeypatch, other_key)
    token = make_id_token(signing_key)

    assert await make_login().validate_id_token(token) is None


async def test_validate_id_token_requires_numeric_id_claim(monkeypatch) -> None:
    configure(monkeypatch)
    key = gen_rsa_key()
    patch_jwks(monkeypatch, key)
    now = int(time.time())
    claims = {
        "iss": "https://oauth.telegram.org",
        "aud": str(CLIENT_ID),
        "iat": now,
        "exp": now + 3600,
        # нет числового `id`
    }
    token = jwt.encode(claims, key, algorithm="RS256")

    assert await make_login().validate_id_token(token) is None


async def test_exchange_code_goes_through_relay(monkeypatch) -> None:
    """Обмен code идёт через релей TG-микросервиса, а не напрямую в Telegram."""
    configure(monkeypatch)
    key = gen_rsa_key()
    patch_jwks(monkeypatch, key)
    patch_relay(monkeypatch, FakeResponse(json_data={"id_token": make_id_token(key)}))

    claims = await make_login().exchange_code("code123", "verifier456")

    assert claims is not None
    assert claims["id"] == 987654321
    sent = FakeAsyncClient.captured
    assert sent["url"] == f"{settings.telegram_service_url}/api/oauth/token"
    assert sent["headers"] == {"X-Api-Key": settings.telegram_service_api_key}
    # Basic auth (client_id:client_secret) идёт транзитом через релей.
    assert sent["auth"] == (str(CLIENT_ID), CLIENT_SECRET)
    assert sent["json"]["grant_type"] == "authorization_code"
    assert sent["json"]["code"] == "code123"
    assert sent["json"]["code_verifier"] == "verifier456"
    assert sent["json"]["client_id"] == str(CLIENT_ID)


async def test_exchange_code_relay_error_returns_none(monkeypatch) -> None:
    """Релей вернул ошибку Telegram (например, невалидный code) → None."""
    configure(monkeypatch)
    patch_relay(monkeypatch, FakeResponse(status_code=400, json_data={"error": "invalid_grant"}))

    assert await make_login().exchange_code("bad-code", "v") is None


async def test_exchange_code_relay_unreachable_returns_none(monkeypatch) -> None:
    """TG-микросервис недоступен → None (юзера редиректит в панель с ошибкой)."""
    configure(monkeypatch)

    class ConnectFailedClient(FakeAsyncClient):
        async def post(self, _url, **_kwargs):
            raise httpx.ConnectError("relay unreachable")

    monkeypatch.setattr("services.telegram_oidc.httpx.AsyncClient", ConnectFailedClient)

    assert await make_login().exchange_code("code", "v") is None


async def test_exchange_code_without_id_token_returns_none(monkeypatch) -> None:
    configure(monkeypatch)
    patch_relay(monkeypatch, FakeResponse(json_data={"access_token": "nope"}))

    assert await make_login().exchange_code("code", "v") is None


def test_extract_username() -> None:
    # Telegram шлёт `username`; на случай смены схемы — фолбэк на OIDC `preferred_username`.
    assert TelegramLoginService.extract_username({"id": 1, "username": "john"}) == "john"
    assert TelegramLoginService.extract_username({"id": 1, "preferred_username": "johndoe"}) == "johndoe"
    assert TelegramLoginService.extract_username({"id": 1, "username": "john", "preferred_username": "x"}) == "john"
    assert TelegramLoginService.extract_username({"id": 1}) is None
    assert TelegramLoginService.extract_username({"id": 1, "username": ""}) is None


def test_proxied_jwk_client_fetches_via_relay(monkeypatch) -> None:
    """_ProxiedJWKClient тянет JWKS через релей с X-Api-Key, а не напрямую."""
    from services.telegram_oidc import TELEGRAM_JWKS_URL, _ProxiedJWKClient

    captured: dict = {}

    class FakeSyncClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse(json_data={"keys": []})

    monkeypatch.setattr("services.telegram_oidc.httpx.Client", FakeSyncClient)

    client = _ProxiedJWKClient(TELEGRAM_JWKS_URL)
    data = client.fetch_data()

    assert data == {"keys": []}
    assert captured["url"] == f"{settings.telegram_service_url}/api/oauth/jwks"
    assert captured["headers"] == {"X-Api-Key": settings.telegram_service_api_key}
