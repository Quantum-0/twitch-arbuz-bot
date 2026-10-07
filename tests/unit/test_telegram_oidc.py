"""Юнит-тесты Telegram Login (OIDC): PKCE, auth URL, валидация id_token по JWKS."""

import base64
import hashlib
import time
from datetime import UTC, datetime, timedelta

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
