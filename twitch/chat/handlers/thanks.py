import random
import re

from opentelemetry import trace

from database.models import TwitchUserSettings, User
from schemas.twitch import ChatMessageWebhookEventSchema
from twitch.chat.handlers.handlers import (
    CommonMessagesHandler,
    HandlerResult,
)

tracer = trace.get_tracer(__name__)


_THANKS_WORDS = {
    "спасибо",
    "спс",
    "сяп",
    "сяб",
    "сябки",
    "пасиба",
    "пасибки",
    "благодарю",
    "спасиб",
    "спасибки",
    "thanks",
    "thx",
    "ty",
    "thank",
}


class ThanksHandler(CommonMessagesHandler):
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema) -> HandlerResult:
        text = message.message.text.lower()
        # Если сообщение адресовано боту (реплай на бота или упоминание)
        addressed_to_bot = (
            message.reply and message.reply.parent_user_name == "quantum075bot"
        ) or "@quantum075bot" in text
        if not addressed_to_bot:
            return HandlerResult.SKIPED

        # Убираем упоминание бота из текста для проверки
        cleaned = re.sub(r"@quantum075bot", "", text).strip()
        if not cleaned:
            return HandlerResult.SKIPED

        # Проверяем что хотя бы одно слово — благодарность
        # (сообщения вида "спасибо боту за помощь" тоже ловим)
        words = re.findall(r"[a-zа-яё]+", cleaned)
        if not any(w in _THANKS_WORDS for w in words):
            return HandlerResult.SKIPED

        user = message.chatter_user_name
        replies = [
            f"@{user}, да не за что! <3",
            f"@{user}, всегда пожалуйста! >w<",
            f"@{user}, обращайся ещё! :3",
            f"@{user}, мне было очень приятно тебе помочь!",
            f"@{user}, рад был помочь!",
        ]
        await self.send_response(chat=streamer, message=random.choice(replies))
        return HandlerResult.HANDLED
