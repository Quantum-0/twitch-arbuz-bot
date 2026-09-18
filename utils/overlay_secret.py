"""Утилиты для overlay_secret — per-user секретного ключа оверлеев.

Для существующих пользователей (без overlay_secret) лениво генерируется
из slovotron_secret (uuid3) — совместимо со старым slovotron-кодом.
Новые регистрации получают случайный uuid4. Сброс — тоже uuid4.
"""

from __future__ import annotations

from uuid import UUID, uuid3, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import User


def compute_legacy_overlay_secret(login_name: str) -> UUID:
    """Вычислить overlay_secret в стиле старого slovotron-кода (uuid3).

    Используется для ленивой миграции существующих пользователей, у которых
    overlay_secret ещё не установлен в БД. Значение совпадает с тем, что
    раньше вычислялось на лету для slovotron.
    """
    return uuid3(namespace=settings.slovotron_secret, name=login_name)


async def ensure_overlay_secret(db: AsyncSession, user: User) -> UUID:
    """Вернуть overlay_secret пользователя, при необходимости создав его.

    Если ``user.overlay_secret`` уже установлен — возвращает его.
    Иначе вычисляет legacy-значение (uuid3 из slovotron_secret + login_name),
    сохраняет в БД и возвращает.

    Используется в overlay-роутах и temp-commands API.
    """
    if user.overlay_secret is not None:
        return user.overlay_secret

    secret = compute_legacy_overlay_secret(user.login_name)
    user.overlay_secret = secret
    await db.commit()
    return secret


async def reset_overlay_secret(db: AsyncSession, user: User) -> UUID:
    """Сгенерировать новый случайный overlay_secret и сохранить в БД."""
    new_secret = uuid4()
    user.overlay_secret = new_secret
    await db.commit()
    return new_secret
