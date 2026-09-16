"""Сервис обновления Twitch user access токенов.

Шаблон — :class:`services.memes_v2.MemealertsOAuthService`: per-user Redis-лок,
порог 10 минут до истечения, ретраи на server_error/сеть, обнуление при invalid_grant.

Twitch refresh token живёт ~14 дней,
поэтому храним только ``twitch_token_expires_at`` access token'а.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import redis.asyncio as aioredis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import User
from utils.cryptography import encrypt_value

logger = logging.getLogger(__name__)

# Порог обновления: если access token живёт меньше этого — обновляем (сек).
_REFRESH_THRESHOLD_SECONDS = 600
# Per-user лок: чтобы параллельные воркеры не обновляли один токен одновременно.
_LOCK_TIMEOUT = 60
# Ретраи для server_error и сетевых ошибок (сек).
_RETRY_BACKOFFS: tuple[int, ...] = (1, 2, 4, 8, 10)


class TwitchTokenRefreshError(Exception):
    """Ошибка обновления Twitch токена (RFC 6749 §5.2)."""

    def __init__(self, error: str, description: str = "", status_code: int = 0):
        self.error: str = error
        self.description: str = description
        self.status_code: int = status_code
        super().__init__(f"{error} ({status_code}): {description}" if description else f"{error} ({status_code})")


class TwitchTokenExpiredError(TwitchTokenRefreshError):
    """Refresh токен Twitch истёк/отозван — требуется повторная авторизация."""


class TwitchTokenService:
    """Единый механизм работы с пользовательскими Twitch токенами.

    Используется во всех местах, где нужен актуальный user access token:
    EventSub, Get Clips, Get Clips Download, chat operations и т.д.
    """

    # Коды ошибок токен-эндпоинта, которые безопасно ретраить.
    _RETRYABLE_ERRORS: frozenset[str] = frozenset({"server_error"})

    def __init__(
        self,
        db_session_factory: Callable[[], AsyncSession],
        redis: aioredis.Redis | None = None,
    ):
        self._db_session_factory = db_session_factory
        self._redis = redis
        self._refresh_semaphore = asyncio.Semaphore(10)

    def startup(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    # ── Публичные методы ──────────────────────────────────────────────────

    async def get_valid_access_token(self, user: User) -> str:
        """Вернуть актуальный Twitch access token пользователя.

        При необходимости (до истечения < 10 мин) — обновляет через refresh token.
        Бросает :class:`TwitchTokenExpiredError` если refresh token недействителен.
        """
        access_token = user.access_token
        if access_token is None:
            raise TwitchTokenExpiredError("no_access_token", "У пользователя нет access token")

        expires_at = user.twitch_token_expires_at
        if expires_at is not None:
            now_naive = datetime.now(UTC).replace(tzinfo=None)
            remaining = (expires_at - now_naive).total_seconds()
            if remaining > _REFRESH_THRESHOLD_SECONDS:
                return access_token

        new_tokens = await self._refresh_tokens_if_need(user, block_on_lock=True)
        return new_tokens["access_token"]

    async def run_periodic_update(self) -> None:
        """Фоновая APScheduler-таска: обновляет токены, истекающие в ближайшее время."""
        logger.info("Запуск периодического обновления Twitch токенов")
        # Выбираем пользователей с заполненным expires_at, где до истечения < 1 день.
        threshold = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1)
        q = sa.select(User).where(
            User.twitch_token_expires_at.is_not(None),
            User.twitch_token_expires_at < threshold,
        )
        async with self._db_session_factory() as db:
            users: Sequence[User] = (await db.execute(q)).scalars().all()

        if not users:
            return

        logger.info("Найдено %d пользователей с истекающими Twitch токенами", len(users))

        results = await asyncio.gather(
            *(self._refresh_tokens_if_need(u, block_on_lock=False) for u in users),
            return_exceptions=True,
        )

        success_count = sum(1 for r in results if not isinstance(r, Exception))
        logger.info("Периодическое обновление Twitch токенов завершено: %d/%d успешно", success_count, len(users))

    # ── Внутренние методы ──────────────────────────────────────────────────

    async def _refresh_tokens_if_need(self, user: User, *, block_on_lock: bool) -> dict[str, Any]:
        """Обновить токен если в этом есть необходимость.

        :param block_on_lock: True — ждать лок (runtime-запросы),
            False — пропустить обновление, если лок занят (фоновая крон-таска).
        """
        refresh_token = user.refresh_token
        if refresh_token is None:
            raise TwitchTokenExpiredError("no_refresh_token", "У пользователя нет refresh token")

        expires_at = user.twitch_token_expires_at
        if expires_at is not None:
            now_naive = datetime.now(UTC).replace(tzinfo=None)
            remaining = (expires_at - now_naive).total_seconds()
            if remaining > _REFRESH_THRESHOLD_SECONDS:
                return {"access_token": user.access_token, "refresh_token": refresh_token}

        async with self._acquire_refresh_lock(user.id, block=block_on_lock) as acquired:
            if not acquired and not block_on_lock:
                logger.debug("Refresh lock занят для user_id=%s, пропускаем", user.id)
                return {"access_token": user.access_token, "refresh_token": refresh_token}

            # Перечитываем токен из БД: другой воркер мог обновить его, пока мы ждали лок.
            fresh_user = await self._load_user(user.id)
            if fresh_user is not None:
                user = fresh_user
                refresh_token = user.refresh_token
                if refresh_token is None:
                    raise TwitchTokenExpiredError("no_refresh_token", "У пользователя нет refresh token")

            expires_at = user.twitch_token_expires_at
            if expires_at is not None:
                now_naive = datetime.now(UTC).replace(tzinfo=None)
                remaining = (expires_at - now_naive).total_seconds()
                if remaining > _REFRESH_THRESHOLD_SECONDS:
                    return {"access_token": user.access_token, "refresh_token": refresh_token}

            return await self._refresh_with_retry(user, refresh_token)

    async def _refresh_with_retry(self, user: User, refresh_token: str) -> dict[str, Any]:
        """Обновить токен с ретраями на server_error/сеть."""
        last_error: Exception | None = None
        for attempt, delay in enumerate(_RETRY_BACKOFFS):
            try:
                result = await self._request_refresh(refresh_token)
                await self._save_tokens(user.id, result)
                logger.info("Twitch токен обновлён для user_id=%s", user.id)
                return result
            except TwitchTokenRefreshError as exc:
                if exc.error not in self._RETRYABLE_ERRORS:
                    if exc.error == "invalid_grant":
                        logger.warning(
                            "Twitch refresh токен отозван для user_id=%s, требуется повторная авторизация",
                            user.id,
                        )
                        await self._invalidate_tokens(user.id)
                    raise
                last_error = exc
                logger.warning(
                    "server_error обновления Twitch токена для user_id=%s (попытка %d), ретрай через %ds",
                    user.id,
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                logger.warning(
                    "Сетевая ошибка обновления Twitch токена для user_id=%s (попытка %d), ретрай через %ds",
                    user.id,
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)
        if last_error is None:
            raise TwitchTokenRefreshError("unknown", "Неизвестная ошибка при обновлении токена")
        raise last_error

    async def _request_refresh(self, refresh_token: str) -> dict[str, Any]:
        """Обменять refresh_token на новый access_token через Twitch OAuth."""
        async with self._refresh_semaphore, httpx.AsyncClient() as client:
            response = await client.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                timeout=10,
            )

        if not response.is_success:
            error, description = self._parse_token_error(response)
            if response.status_code >= 500:
                error = "server_error"
                if not description:
                    description = response.text[:200]
            logger.warning(
                "Twitch token endpoint error: error=%s, desc=%s, status=%s",
                error,
                description,
                response.status_code,
            )
            raise TwitchTokenRefreshError(error=error, description=description, status_code=response.status_code)

        data = response.json()
        expires_in = data.get("expires_in")
        if expires_in is None:
            logger.error("Twitch вернул ответ без expires_in: %s", list(data.keys()))
            raise TwitchTokenRefreshError("invalid_response", "Нет expires_in в ответе Twitch")

        now_naive = datetime.now(UTC).replace(tzinfo=None)
        new_expires_at = now_naive + timedelta(seconds=expires_in - 1)

        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_at": new_expires_at,
        }

    async def _save_tokens(self, user_id: int, tokens: dict[str, Any]) -> None:
        """Сохранить обновлённые токены в БД.

        Важно: ``sa.update(User).values(access_token=...)`` пишет напрямую в колонку
        ``access_token``, обходя ``@property``-сеттер с шифрованием. Поэтому шифруем
        значения через ``encrypt_value()`` вручную — иначе ``User.access_token`` getter
        вызовет ``decrypt_value()`` на plaintext и упадёт с ``InvalidToken``.
        """
        async with self._db_session_factory() as db:
            await db.execute(
                sa.update(User)
                .where(User.id == user_id)
                .values(
                    access_token=encrypt_value(tokens["access_token"]),
                    refresh_token=encrypt_value(tokens["refresh_token"]),
                    twitch_token_expires_at=tokens["expires_at"],
                )
            )
            await db.commit()

    async def _invalidate_tokens(self, user_id: int) -> None:
        """Обнулить токены пользователя (refresh токен отозван/истёк)."""
        logger.warning("Обнуление Twitch токенов для user_id=%s", user_id)
        async with self._db_session_factory() as db:
            await db.execute(
                sa.update(User)
                .where(User.id == user_id)
                .values(
                    access_token=None,
                    refresh_token=None,
                    twitch_token_expires_at=None,
                )
            )
            await db.commit()

    async def _load_user(self, user_id: int) -> User | None:
        """Перечитать пользователя из БД (для ревалидации под локом)."""
        q = sa.select(User).where(User.id == user_id)
        async with self._db_session_factory() as db:
            return await db.scalar(q)

    @asynccontextmanager
    async def _acquire_refresh_lock(self, user_id: int, *, block: bool) -> AsyncIterator[bool]:
        """Per-user распределённый лок на рефреш токена."""
        acquired = False
        lock = None
        if self._redis is not None:
            lock = self._redis.lock(f"twitch:refresh:{user_id}", timeout=_LOCK_TIMEOUT)
            try:
                acquired = await lock.acquire(blocking=block, blocking_timeout=60 if block else None)
            except (aioredis.ConnectionError, aioredis.TimeoutError):
                logger.warning("Redis недоступен, пропускаем лок для user_id=%s", user_id)
                acquired = False
            except Exception:
                logger.warning("Не удалось захватить лок для user_id=%s", user_id, exc_info=True)
                acquired = False
        try:
            yield acquired
        finally:
            if acquired and lock is not None:
                try:
                    await lock.release()
                except Exception:
                    logger.warning("Не удалось освободить лок для user_id=%s", user_id, exc_info=True)

    @staticmethod
    def _parse_token_error(response: httpx.Response) -> tuple[str, str]:
        """Извлечь error и error_description из тела ошибки токен-эндпоинта."""
        try:
            body = response.json()
        except Exception:
            return "unknown", response.text
        if not isinstance(body, dict):
            return "unknown", str(body)
        error = str(body.get("error") or "unknown")
        description = str(body.get("error_description") or "")
        return error, description

    @staticmethod
    def calc_expires_at(expires_in: int) -> datetime:
        """Вычислить expires_at из expires_in (UTC, без timezone)."""
        now_naive = datetime.now(UTC).replace(tzinfo=None)
        return now_naive + timedelta(seconds=expires_in - 1)
