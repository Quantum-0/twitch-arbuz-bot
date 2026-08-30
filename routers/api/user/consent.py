"""Фиксация согласия с cookies и взаимодействий с boosty-предложением (ФЗ-152)."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth

router = APIRouter(prefix="/consent", tags=["Consent & Boosty"])


@router.post("/cookie-consent")
async def accept_cookie_consent(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User = Security(user_auth),
):
    """Зафиксировать факт согласия пользователя с использованием cookies."""
    user.cookie_consent_at = datetime.utcnow()
    await db.commit()
    return JSONResponse({"title": "Готово", "message": "Согласие с использованием cookies сохранено."}, 200)


@router.post("/boosty/dismiss")
async def dismiss_boosty(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User = Security(user_auth),
):
    """Пользователь закрыл boosty-предложение — не показывать его минимум месяц."""
    user.boosty_dismissed_at = datetime.utcnow()
    await db.commit()
    return JSONResponse({"title": "OK", "message": "Предложение скрыто."}, 200)


@router.post("/boosty/click")
async def click_boosty(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User = Security(user_auth),
):
    """Пользователь перешёл на boosty со страницы Сервиса. Грубая оценка факта подписки.

    Увеличиваем счётчик переходов и сбрасываем dismissed_at, чтобы не показывать
    предложение в течение месяца после клика.
    """
    user.boosty_clicks = (user.boosty_clicks or 0) + 1
    user.boosty_dismissed_at = datetime.utcnow()
    await db.commit()
    return JSONResponse({"title": "OK", "message": "Переход зафиксирован."}, 200)
