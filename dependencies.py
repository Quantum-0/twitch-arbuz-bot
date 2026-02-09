from collections.abc import Generator
from contextlib import asynccontextmanager

from config import settings
from database.database import AsyncSessionLocal, async_engine
from services.ai import OpenAIClient
from services.mqtt import MQTTClient
from services.sse_manager import SSEManager
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch

singletons: dict[str, None | Twitch | ChatBot | OpenAIClient | MQTTClient | SSEManager] = {
    "twitch": None,
    "chat_bot": None,
    "ai": None,
    "mqtt": None,
    "ssem": None,
}


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

@asynccontextmanager
async def lifespan():
    singletons["twitch"] = Twitch()
    singletons["chat_bot"] = ChatBot()
    singletons["ai"] = OpenAIClient()
    singletons["mqtt"] = MQTTClient()
    singletons["ssem"] = SSEManager()
    await singletons["twitch"].startup()
    await singletons["chat_bot"].startup(singletons["twitch"])
    await singletons["ai"].startup()
    if settings.update_bot_channels_on_startup:
        await singletons["chat_bot"].update_bot_channels()

    if not settings.direct_handle_messages:
        singletons["mqtt"].subscribe(
            "twitch/+/message",
            singletons["chat_bot"].on_message,
        )

    async with singletons["mqtt"].lifespan():
        yield

    await async_engine.dispose()

def get_twitch() -> Generator[Twitch]:
    tw: Twitch = singletons["twitch"]  # type: ignore
    if tw:
        yield tw
    else:
        raise RuntimeError("Twitch wasn't initialized")


def get_chat_bot() -> Generator[ChatBot]:
    cb: ChatBot = singletons["chat_bot"]  # type: ignore
    if cb:
        yield cb
    else:
        raise RuntimeError("ChatBot wasn't initialized")

def get_ai() -> Generator[OpenAIClient]:
    ai: OpenAIClient = singletons["ai"]
    if ai:
        yield ai
    else:
        raise RuntimeError("OpenAI client wasn't initialized")

def get_mqtt() -> Generator[MQTTClient]:
    mqtt: MQTTClient = singletons["mqtt"]
    if mqtt:
        yield mqtt
    else:
        raise RuntimeError("MQTT client wasn't initialized")

def get_sse_manager() -> Generator[SSEManager]:
    ssem: SSEManager = singletons["ssem"]
    if ssem:
        yield ssem
    else:
        raise RuntimeError("SSE Manager was not initialized")
