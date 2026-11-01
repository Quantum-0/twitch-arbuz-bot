import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

import sqlalchemy as sa
from opentelemetry.context import Context, attach, detach
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from services.cache import Cache

T = TypeVar("T")

logger = logging.getLogger(__name__)

USERS_COUNT_CACHE_KEY = "landing:users_count"
USERS_COUNT_CACHE_TTL = 3600


async def get_users_count(db: AsyncSession, cache: Cache) -> int | None:
    """Число зарегистрированных пользователей для JSON-LD лендинга (Redis-кэш, 1 час)."""
    try:
        cached = await cache.get_str(USERS_COUNT_CACHE_KEY)
        if cached is not None:
            return int(cached)
        count = (await db.execute(sa.select(sa.func.count(User.id)))).scalar_one()
        await cache.set_str(USERS_COUNT_CACHE_KEY, str(count), ttl=USERS_COUNT_CACHE_TTL)
        return count
    except Exception:
        logger.warning("Landing users count unavailable", exc_info=True)
        return None


async def call_with_delay(timer: float, func: Awaitable[T]) -> T:
    await asyncio.sleep(timer)
    return await func


async def run_in_clean_otel_context(coro):
    """Сбрасывает текущий контекст OpenTelemetry и запускает корутину в новом трейсе"""
    token = attach(Context())  # Полный разрыв связи
    try:
        await coro
    finally:
        detach(token)
