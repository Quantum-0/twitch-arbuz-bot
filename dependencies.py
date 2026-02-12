from collections.abc import Generator
from contextlib import asynccontextmanager, contextmanager

from config import settings
from database.database import AsyncSessionLocal, async_engine
from services.ai import OpenAIClient
from services.eventsub_service import TwitchEventSubService
from services.image_resizer import ImageResizer
from services.mqtt import MQTTClient
from services.sse_manager import SSEManager
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch

singletons: dict[str, None | Twitch | ChatBot | OpenAIClient | MQTTClient | SSEManager | TwitchEventSubService] = {
    "twitch": None,
    "chat_bot": None,
    "ai": None,
    "mqtt": None,
    "ssem": None,
    "tevsub": None,
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

    singletons["tevsub"] = TwitchEventSubService(
        twitch=singletons["twitch"],
        chatbot=singletons["chat_bot"],
        ai=singletons["ai"],
        ssem=singletons["ssem"],
        img_resizer=ImageResizer(),
    )

    if not settings.direct_handle_messages:
        singletons["mqtt"].subscribe(
            "twitch/+/message",
            singletons["chat_bot"].on_message,
        )

    if not settings.direct_handle_rewards:
        singletons["mqtt"].subscribe(
            "twitch/+/reward-redemption",
            singletons["tevsub"].handle_reward_redemption,
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

def get_ai():
    ai = singletons.get("ai")
    if not ai:
        raise RuntimeError("OpenAI client wasn't initialized")
    return ai

def get_mqtt() -> Generator[MQTTClient]:
    mqtt: MQTTClient = singletons["mqtt"]
    if mqtt:
        yield mqtt
    else:
        raise RuntimeError("MQTT client wasn't initialized")

def get_sse_manager():
    ssem = singletons.get("ssem")
    if not ssem:
        raise RuntimeError("SSE Manager wasn't initialized")
    return ssem

def get_twitch_eventsub_service():
    service = singletons["tevsub"]
    if not service:
        raise RuntimeError("Service is not initialized")
    return service
