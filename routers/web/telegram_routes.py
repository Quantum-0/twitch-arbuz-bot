"""Telegram Login (OpenID Connect) — привязка личного Telegram-аккаунта.

Флоу:
- ``GET /auth/telegram`` — генерация state + PKCE-пары (в сессию), редирект
  на oauth.telegram.org (только для залогиненных).
- ``GET /auth/telegram/callback`` — приём ``code``/``state``, обмен на id_token,
  валидация (JWKS, RS256/ES256), сохранение ``telegram_user_id`` в
  ``TelegramSettings``, редирект на ``/panel``.

Отвязка личного аккаунта (POST /api/user/telegram/unlink-account) —
см. ``routers/api/user/telegram.py``.
"""

import logging
import secrets
from typing import Annotated, Any

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import RedirectResponse

from container import Container
from dependencies import get_db
from routers.security_helpers import user_auth
from services.telegram_oidc import TelegramLoginService
from utils.telegram import ensure_telegram_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["Service"])

# Ключи сессии для OIDC state / PKCE-верифатора (anti-CSRF + anti-replay).
_SESSION_STATE_KEY = "tg_oidc_state"
_SESSION_VERIFIER_KEY = "tg_oidc_verifier"


@router.get("/auth/telegram", response_class=RedirectResponse)
@inject
async def telegram_auth(
    request: Request,
    login: Annotated[TelegramLoginService, Depends(Provide[Container.telegram_login_service])],
    _: Any = Security(user_auth),
):
    """Инициировать привязку Telegram — редирект на oauth.telegram.org."""
    if not login.is_configured:
        logger.warning("Telegram Login не настроен (telegram_login_client_id/secret), редирект в /panel")
        return RedirectResponse(url="/panel")

    state = secrets.token_urlsafe(24)
    verifier = login.generate_pkce_verifier()
    request.session[_SESSION_STATE_KEY] = state
    request.session[_SESSION_VERIFIER_KEY] = verifier
    return RedirectResponse(login.build_auth_url(state, login.pkce_challenge(verifier)))


@router.get("/auth/telegram/callback", response_class=RedirectResponse)
@inject
async def telegram_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    login: Annotated[TelegramLoginService, Depends(Provide[Container.telegram_login_service])],
    user: Any = Security(user_auth),
    code: str | None = None,
    state: str | None = None,
):
    """Приём ответа oauth.telegram.org: state → обмен code → telegram_user_id."""
    expected_state = request.session.get(_SESSION_STATE_KEY)
    verifier = request.session.pop(_SESSION_VERIFIER_KEY, None)
    request.session.pop(_SESSION_STATE_KEY, None)

    if (
        not code
        or not state
        or not verifier
        or not expected_state
        or not secrets.compare_digest(state, expected_state)
    ):
        logger.warning("Telegram callback: invalid state or missing code (user_id=%s)", user.id)
        return RedirectResponse(url="/panel")

    claims = await login.exchange_code(code, verifier)
    if claims is None:
        logger.warning("Telegram callback: token exchange/validation failed (user_id=%s)", user.id)
        return RedirectResponse(url="/panel")

    tg = await ensure_telegram_settings(db, user)
    tg.telegram_user_id = str(claims["id"])
    await db.commit()
    logger.info("Telegram linked: user_id=%s telegram_user_id=%s", user.id, tg.telegram_user_id)
    return RedirectResponse(url="/panel")
