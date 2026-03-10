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


@asynccontextmanager
async def lifespan(app: "FastAPI | None" = None):
    from container import Container

    container = Container()
    set_container(container)
    container.wire()

    twitch = container.twitch()
    chat_bot = container.chat_bot()
    ai = container.ai()
    mqtt = container.mqtt()

    await twitch.startup()
    await chat_bot.startup(twitch)
    await ai.startup()

    if settings.update_bot_channels_on_startup:
        await chat_bot.update_bot_channels()

    eventsub_service = container.twitch_eventsub_service()

    if not settings.direct_handle_messages:
        mqtt.subscribe("twitch/+/message", chat_bot.on_message)

    if not settings.direct_handle_rewards:
        mqtt.subscribe("twitch/+/reward-redemption", eventsub_service.handle_reward_redemption)

    if app is not None:
        app.container = container

    async with mqtt.lifespan():
        yield

    await async_engine.dispose()
    container.unwire()
    set_container(None)
