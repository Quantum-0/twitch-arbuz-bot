from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

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
    scheduler = container.scheduler()
    memealerts_auth = container.memealerts_auth()
    stickers_processor = container.stickers_processor()

    await state_manager.startup(redis)
    await cache.startup(redis, binary_redis)
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
