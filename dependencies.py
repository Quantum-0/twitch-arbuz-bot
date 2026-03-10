from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager

from database.database import async_engine
from container import Container
from config import settings
from services.ai import OpenAIClient
from services.eventsub_service import TwitchEventSubService
from services.mqtt import MQTTClient
from services.sse_manager import SSEManager
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch

_container: Container | None = None


def get_container() -> Container:
    if _container is None:
        raise RuntimeError("Container was not initialized")
    return _container


async def get_db() -> AsyncGenerator:
    async with get_container().db_session_factory() as session:
        yield session


def get_twitch() -> Generator[Twitch, None, None]:
    yield get_container().twitch()


def get_chat_bot() -> Generator[ChatBot, None, None]:
    yield get_container().chat_bot()


def get_ai() -> OpenAIClient:
    return get_container().ai()


def get_mqtt() -> Generator[MQTTClient, None, None]:
    yield get_container().mqtt()


def get_sse_manager() -> SSEManager:
    return get_container().sse_manager()


def get_twitch_eventsub_service() -> TwitchEventSubService:
    return get_container().twitch_eventsub_service()


@asynccontextmanager
async def lifespan(app=None):
    global _container

    container = Container()
    _container = container
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
    _container = None
