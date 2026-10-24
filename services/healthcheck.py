"""Healthcheck связанных сервисов для панели /server (ручка /api/admin/healthcheck).

Каждая проверка — дешёвый запрос (SELECT 1 / PING / GET health-эндпоинта)
с общим таймаутом CHECK_TIMEOUT_S. Проверки выполняются параллельно,
сбой или зависание одной не влияет на остальные.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx
import redis.asyncio as aioredis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from schemas.api import HealthcheckResponseSchema, HealthcheckServiceSchema
from services.mqtt import MQTTClient

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 5.0


async def _run_check(name: str, check: Callable[[], Awaitable[str]]) -> HealthcheckServiceSchema:
    """Выполнить одну проверку с таймаутом и вернуть её статус."""
    start = time.perf_counter()
    try:
        detail = await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
    except TimeoutError:
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.warning("Healthcheck `%s` timed out after %.0fs", name, CHECK_TIMEOUT_S)
        return HealthcheckServiceSchema(status="error", latency_ms=latency_ms, error=f"таймаут {CHECK_TIMEOUT_S:.0f}с")
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.warning("Healthcheck `%s` failed: %s", name, exc, exc_info=True)
        return HealthcheckServiceSchema(status="error", latency_ms=latency_ms, error=f"{type(exc).__name__}: {exc}")
    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    return HealthcheckServiceSchema(status="ok", latency_ms=latency_ms, detail=detail)


async def check_db(db_session_factory: Callable[[], AsyncSession]) -> str:
    """PostgreSQL: простейший запрос."""
    async with db_session_factory() as session:
        await session.execute(sa.text("SELECT 1"))
    return "SELECT 1 — ok"


async def check_redis(redis: aioredis.Redis) -> str:
    """Redis: PING."""
    if not await redis.ping():
        raise ConnectionError("PING не вернул PONG")
    return "PING — ok"


async def check_mqtt(mqtt: MQTTClient) -> str:
    """MQTT/EMQX: подключение установлено и цикл обработки сообщений жив."""
    if not mqtt.is_alive:
        raise ConnectionError("нет подключения к брокеру (клиент не подключён или цикл обработки сообщений умер)")
    return "подключён, цикл обработки жив"


async def check_tts() -> str:
    """Внешний TTS-сервер: GET /health (аналогично /api/user/check/tts-server)."""
    if not settings.tts.api_url:
        raise RuntimeError("TTS не настроен (пустой tts.api_url)")
    parts = urlsplit(settings.tts.api_url)
    health_url = urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as client:
        resp = await client.get(health_url)
    if resp.status_code != 200:
        raise RuntimeError(f"TTS-сервер вернул {resp.status_code}")
    body = resp.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"TTS-сервер сообщил об ошибке: {body}")
    if not body.get("rvc_available"):
        raise RuntimeError("TTS-сервер запущен, но RVC-сервер недоступен")
    return "health: ok, RVC доступен"


async def check_telegram() -> str:
    """TG-микросервис (twibot-tg): GET /api/healthcheck."""
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as client:
        resp = await client.get(f"{settings.telegram.service_url}/api/healthcheck")
    if resp.status_code != 200:
        raise RuntimeError(f"TG-сервис вернул {resp.status_code}")
    return "healthcheck: 200"


async def check_twitch() -> str:
    """Twitch API: валидация токена бота (заодно проверяет, что id.twitch.tv доступен)."""
    token = settings.twitch.bot_access_token.get_secret_value()
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as client:
        resp = await client.get(
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code == 401:
        raise RuntimeError("токен бота невалиден (401)")
    if resp.status_code != 200:
        raise RuntimeError(f"Twitch вернул {resp.status_code}")
    login = resp.json().get("login")
    return f"токен бота валиден ({login})"


async def run_healthchecks(
    db_session_factory: Callable[[], AsyncSession],
    redis: aioredis.Redis,
    mqtt: MQTTClient,
) -> HealthcheckResponseSchema:
    """Прогнать все проверки параллельно и собрать ответ для /api/admin/healthcheck."""
    checks: dict[str, Callable[[], Awaitable[str]]] = {
        "db": lambda: check_db(db_session_factory),
        "redis": lambda: check_redis(redis),
        "mqtt": lambda: check_mqtt(mqtt),
        "tts": check_tts,
        "telegram": check_telegram,
        "twitch": check_twitch,
    }
    results: list[HealthcheckServiceSchema] = await asyncio.gather(
        *(_run_check(name, check) for name, check in checks.items())
    )
    services = dict(zip(checks.keys(), results, strict=True))
    overall = "ok" if all(s.status == "ok" for s in services.values()) else "error"
    return HealthcheckResponseSchema(overall=overall, services=services)
