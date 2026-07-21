from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import logging

from config import settings
from container_runtime import get_container, set_container
from database.database import async_engine

if TYPE_CHECKING:
    from fastapi import FastAPI


async def get_db() -> AsyncGenerator:
    session_factory = get_container().db_session_factory()
    async with session_factory() as session:
        yield session


# TODO: inject по аналогии с ручками, но без depends, просто Provide[Container....]
# TODO: не забыть добавить вайринг
async def test_bg_task():
    print("test")


@asynccontextmanager
async def lifespan(app: "FastAPI | None" = None):
    from container import Container

    container = Container()
    set_container(container)
    container.wire()
    await container.init_resources()

    redis = await container.redis()
    binary_redis = await container.binary_redis()
    twitch = container.twitch()
    chat_bot = container.chat_bot()
    ai = container.ai()
    mqtt = container.mqtt()
    slovotron = container.slovotron()
    state_manager = container.state_manager()
    cache = container.cache()
    statistics = container.statistics()
    sse_manager = container.sse_manager()
    scheduler = container.scheduler()
    memealerts_auth = container.memealerts_auth()
    stickers_processor = container.stickers_processor()

    await state_manager.startup(redis)
    await cache.startup(redis, binary_redis)
    await statistics.startup(redis)
    await sse_manager.startup(redis)
    await twitch.startup()
    await chat_bot.startup(twitch)
    await ai.startup()
    await stickers_processor.start()

    if settings.update_bot_channels_on_startup:
        await chat_bot.update_bot_channels()

    eventsub_service = container.twitch_eventsub_service()

    if not settings.direct_handle_messages:
        mqtt.subscribe("twitch/+/message", chat_bot.on_message)

    if not settings.direct_sending_messages:
        # mqtt.subscribe("/twitch/outgoing/chat/+", chat_bot.send_message_from_broker)
        raise NotImplementedError
        # TODO: перенести топики в константы

    if not settings.direct_handle_rewards:
        mqtt.subscribe("twitch/+/reward-redemption", eventsub_service.handle_reward_redemption)

    mqtt.subscribe("slovotron/+/+", slovotron.handle_webhook)

    if app is not None:
        app.container = container

    scheduler.add_job(
        memealerts_auth.run_periodic_update,
        trigger="cron",
        hour="*/3",
        minute="0",
        second="0",
        id="update_memealerts_tokens",
        replace_existing=True,
    )
    # Дамп 10-минутных бакетов статистики из Redis в БД.
    scheduler.add_job(
        statistics.flush_to_db,
        trigger="cron",
        minute="*/10",
        second="0",
        id="flush_statistics",
        replace_existing=True,
    )
    # Суточная очистка статистики старше RETENTION_DAYS (~3 месяца).
    scheduler.add_job(
        statistics.cleanup_old_data,
        trigger="cron",
        hour="4",
        minute="0",
        second="0",
        id="cleanup_old_statistics",
        replace_existing=True,
    )
    # Ежеминутный snapshot активных SSE-подключений (gauge-метрика).
    # Раз в минуту берём мгновенное состояние из SSEManager и перезаписываем
    # значение в Redis-хэше текущего бакета (через set_gauge, не инкремент).
    # При следующем flush_to_db это значение попадёт в БД как последнее
    # наблюдаемое в рамках бакета.
    from schemas.api import StatsType

    async def snapshot_sse_job() -> None:
        try:
            values = await sse_manager.snapshot()
            statistics.set_gauge(StatsType.SSE_CONNECTIONS, values)
        except Exception:
            logging.getLogger(__name__).error("snapshot_sse_job failed", exc_info=True)

    scheduler.add_job(
        snapshot_sse_job,
        trigger="cron",
        minute="*",
        second="0",
        id="snapshot_sse",
        replace_existing=True,
    )
    scheduler.start()
    print("Планировщик запущен")

    async with mqtt.lifespan(), state_manager.lifespan():
        yield

    scheduler.shutdown()
    print("Планировщик остановлен")

    await stickers_processor.stop()
    await async_engine.dispose()
    container.unwire()
    set_container(None)
