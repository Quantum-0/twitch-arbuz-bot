"""Telegram Login (OpenID Connect) — привязка личного Telegram-аккаунта.

Флоу (Authorization Code + PKCE S256, core.telegram.org/bots/telegram-login):

1. ``GET /auth/telegram`` — генерируем state + PKCE-пару, кладём в сессию
   пользователя, редиректим на ``https://oauth.telegram.org/auth``.
2. Telegram редиректит обратно на ``redirect_uri`` (``/auth/telegram/callback``)
   с ``code`` и ``state``.
3. ``exchange_code`` — POST на TG-микросервис ``/api/oauth/token`` (релей
   ``https://oauth.telegram.org/token``; у хоста основного сервиса нет
   доступа к Telegram, Basic auth client_id:client_secret идёт транзитом)
   → ``id_token`` (JWT).
4. ``validate_id_token`` — проверка подписи (RS256/ES256) по JWKS
   (``_ProxiedJWKClient`` — JWKS тоже тянет релеем через TG-микросервис
   ``/api/oauth/jwks``), ``iss``, ``aud`` == client_id, ``exp`` (PyJWT).
   Из claims берём ``id`` — Telegram user id юзера (он же chat_id его
   личного чата с ботом) → ``TelegramSettings.telegram_user_id`` и
   ``username``/``preferred_username`` → ``telegram_username``.

Client ID/Secret выдаёт @BotFather (Login Widget → Allowed URLs). Если не
заданы в конфиге — ``is_configured`` False, флоу отключён.
"""

import asyncio
import base64
import hashlib
import logging
import secrets
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_AUTH_ENDPOINT = "https://oauth.telegram.org/auth"
# Релей-эндпоинты TG-микросервиса (twibot-tg): у хоста основного сервиса
# нет доступа к серверам Telegram, обмен code и JWKS идут через него.
TG_RELAY_TOKEN_PATH = "/api/oauth/token"  # noqa: S105
TG_RELAY_JWKS_PATH = "/api/oauth/jwks"
# URI оригинального JWKS-эндпоинта — используется только как идентификатор
# в _ProxiedJWKClient (реальный запрос идёт через релей).
TELEGRAM_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
TELEGRAM_ISSUER = "https://oauth.telegram.org"

# openid обязателен по спеке; profile — имя/username в id_token;
# telegram:bot_access — разрешение боту писать юзеру в личку
# (нужно для персональных уведомлений о проблемах интеграций).
TELEGRAM_LOGIN_SCOPES = "openid profile telegram:bot_access"

# PyJWKClient с проксированным fetch — держим один экземпляр на процесс
# (кеширование ключей и подбор по kid — как в штатном PyJWKClient).
_jwk_client: PyJWKClient | None = None


class _ProxiedJWKClient(PyJWKClient):
    """PyJWKClient, который тянет JWKS через TG-микросервис (релей).

    У хоста основного сервиса нет прямого доступа к Telegram, поэтому
    ``fetch_data`` запрашивает ``{telegram_service_url}/api/oauth/jwks``
    вместо оригинального ``oauth.telegram.org/.well-known/jwks.json``.
    Метод синхронный (контракт PyJWKClient), вызывается из
    ``asyncio.to_thread`` в ``validate_id_token``.
    """

    def fetch_data(self) -> Any:
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    f"{settings.telegram_service_url}{TG_RELAY_JWKS_PATH}",
                    headers={"X-Api-Key": settings.telegram_service_api_key},
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Контракт PyJWKClient: сетевые ошибки — PyJWKClientConnectionError
            # (подкласс PyJWKClientError), чтобы вызывающий код различал
            # недоступность JWKS и невалидный токен.
            raise jwt.PyJWKClientConnectionError(f"Failed to fetch JWKS via TG relay: {exc}") from exc


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = _ProxiedJWKClient(TELEGRAM_JWKS_URL)
    return _jwk_client


class TelegramLoginService:
    """OIDC-клиент Telegram Login: построение auth-URL, обмен code, валидация id_token."""

    @staticmethod
    def generate_pkce_verifier() -> str:
        """Сгенерировать PKCE code_verifier (base64url, 43+ символа)."""
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")

    @staticmethod
    def pkce_challenge(verifier: str) -> str:
        """S256 code_challenge для code_verifier (base64url без padding)."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @property
    def is_configured(self) -> bool:
        """Заданы ли client_id/client_secret (флоу привязки доступен)."""
        return settings.telegram_login_client_id is not None and bool(
            settings.telegram_login_client_secret.get_secret_value()
        )

    def build_auth_url(self, state: str, code_challenge: str) -> str:
        """Сформировать URL инициации авторизации на oauth.telegram.org."""
        params = {
            "client_id": str(settings.telegram_login_client_id),
            "redirect_uri": str(settings.telegram_login_redirect_url),
            "response_type": "code",
            "scope": TELEGRAM_LOGIN_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{TELEGRAM_AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any] | None:
        """Обменять authorization code на id_token и вернуть валидированные claims.

        Запрос идёт через релей TG-микросервиса (``/api/oauth/token``):
        Basic auth client_id:client_secret пробрасывается транзитом.
        ``None`` при любой ошибке (сеть/HTTP/невалидный токен) — вызывающий
        код просто редиректит юзера обратно в панель.
        """
        if not self.is_configured:
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{settings.telegram_service_url}{TG_RELAY_TOKEN_PATH}",
                    json={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": str(settings.telegram_login_redirect_url),
                        "client_id": str(settings.telegram_login_client_id),
                        "code_verifier": code_verifier,
                    },
                    headers={"X-Api-Key": settings.telegram_service_api_key},
                    auth=(
                        str(settings.telegram_login_client_id),
                        settings.telegram_login_client_secret.get_secret_value(),
                    ),
                )
                response.raise_for_status()
                token_data: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("Telegram Login: token exchange failed (via TG relay)", exc_info=True)
            return None

        id_token = token_data.get("id_token")
        if not isinstance(id_token, str):
            logger.warning("Telegram Login: token response without id_token: %s", token_data)
            return None
        return await self.validate_id_token(id_token)

    async def validate_id_token(self, id_token: str) -> dict[str, Any] | None:
        """Проверить подпись и claims id_token, вернуть payload claims.

        Проверяются: подпись (RS256/ES256 по JWKS), ``iss``, ``aud`` == client_id,
        ``exp``/``iat`` (PyJWT). Дополнительно требуем числовой claim ``id`` —
        Telegram user id (совпадает с chat_id личного чата с ботом).
        """
        if not self.is_configured:
            return None
        try:
            # PyJWKClient ходит за ключами синхронно — уводим в тред,
            # чтобы не блокировать event loop (ключи кешируются после первого раза).
            fetch_key: Callable[..., Any] = _get_jwk_client().get_signing_key_from_jwt
            signing_key = await asyncio.to_thread(fetch_key, id_token)
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=str(settings.telegram_login_client_id),
                issuer=TELEGRAM_ISSUER,
            )
        except jwt.PyJWKClientError:
            # Включая PyJWKClientConnectionError — релей/JWKS недоступны.
            logger.warning("Telegram Login: JWKS fetch failed (via TG relay)", exc_info=True)
            return None
        except jwt.PyJWTError:
            logger.warning("Telegram Login: id_token validation failed", exc_info=True)
            return None
        except Exception:
            logger.error("Telegram Login: unexpected id_token validation error", exc_info=True)
            return None

        if not isinstance(claims.get("id"), int):
            logger.warning("Telegram Login: id_token without numeric `id` claim: %s", claims)
            return None
        return claims

    @staticmethod
    def extract_username(claims: dict[str, Any]) -> str | None:
        """Username из claims id_token.

        Telegram шлёт claim ``username``; на случай смены схемы читаем и
        стандартный OIDC ``preferred_username``.
        """
        username = claims.get("username") or claims.get("preferred_username")
        return str(username) if username else None
