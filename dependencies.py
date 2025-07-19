from twitch.bot import ChatBot
from database.database import AsyncSessionLocal
from twitch.twitch import Twitch


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

def get_twitch():
    yield Twitch()

def get_chat_bot():
    yield ChatBot()
