import asyncio
from collections.abc import Generator

from twitch.bot import ChatBot
from database.database import AsyncSessionLocal
from twitch.twitch import Twitch

singletons: dict[str, None | Twitch | ChatBot] = {"twitch": None, "chat_bot": None}


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_and_startup():
    singletons["twitch"] = Twitch()
    singletons["chat_bot"] = ChatBot()
    await singletons["twitch"].startup()
    await singletons["chat_bot"].startup(singletons["twitch"])
    async with AsyncSessionLocal() as session:
        await singletons["chat_bot"].update_bot_channels(session)

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
