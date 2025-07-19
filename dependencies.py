from twitch.bot import ChatBot
from database.database import SessionLocal
from twitch.twitch import Twitch


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_twitch():
    yield Twitch()

def get_chat_bot():
    yield ChatBot()