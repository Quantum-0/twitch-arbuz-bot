import asyncio

from twitch.bot import ChatBot
from database.database import AsyncSessionLocal
from twitch.twitch import Twitch

singletons: dict[str, None | Twitch | ChatBot] = {"twitch": None, "chat_bot": None}


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_and_startup():
    loop = asyncio.get_event_loop()
    singletons["twitch"] = Twitch()
    singletons["chat_bot"] = ChatBot()
    await singletons["twitch"].startup()
    await singletons["chat_bot"].startup(singletons["twitch"], loop)
    await singletons["chat_bot"].update_bot_channels()

def get_twitch() -> Twitch:
    tw = singletons["twitch"]
    if tw:
        yield tw
    else:
        raise RuntimeError("Twitch wasn't initialized")

def get_chat_bot() -> ChatBot:
    cb = singletons["chat_bot"]
    if cb:
        yield cb
    else:
        raise RuntimeError("ChatBot wasn't initialized")
