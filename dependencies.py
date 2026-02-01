from collections.abc import Generator

from config import settings
from database.database import AsyncSessionLocal
from services.ai import OpenAIClient
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch

singletons: dict[str, None | Twitch | ChatBot | OpenAIClient] = {"twitch": None, "chat_bot": None, "ai": None}


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_and_startup():
    singletons["twitch"] = Twitch()
    singletons["chat_bot"] = ChatBot()
    singletons["ai"] = OpenAIClient()
    await singletons["twitch"].startup()
    await singletons["chat_bot"].startup(singletons["twitch"])
    await singletons["ai"].startup()
    if settings.update_bot_channels_on_startup:
        await singletons["chat_bot"].update_bot_channels()


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