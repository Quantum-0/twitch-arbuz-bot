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

# «дай мем…» — покрывает «дай мем / мемов / мемаф / мемафов» и т.п.
_MEMES_RE = re.compile(r"дай\s+мем")


def _memes_match(message: ChatMessageWebhookEventSchema) -> bool:
    """«Дай мемов», адресованное боту (реплай на бота или упоминание)."""
    text = message.message.text.lower()
    addressed_to_bot = (message.reply and message.reply.parent_user_name == "quantum075bot") or "@quantum075bot" in text
    if not addressed_to_bot:
        return False

    cleaned = re.sub(r"@quantum075bot", "", text).strip()
    return bool(_MEMES_RE.search(cleaned))


class GiveMemesHandler(CommonMessagesHandler):
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def is_candidate(self, message: ChatMessageWebhookEventSchema) -> bool:
        return _memes_match(message)

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema) -> HandlerResult:
        if not _memes_match(message):
            return HandlerResult.SKIPED

        user = message.chatter_user_name
        replies = [
            f"@{user}, неть",
            f"@{user}, не дам",
            f"@{user}, не хЪ",
        ]
        # Подключена награда покупки мемкоинов — добавляем наводящие варианты.
        ma = streamer.memealerts
        if ma and ma.memealerts_reward:
            replies += [
                f"@{user}, только через награду 👀",
                f"@{user}, только если хорошо попросишь",
                f"@{user}, воспользуйся наградой!",
            ]
        await self.send_response(chat=streamer, message=random.choice(replies))
        return HandlerResult.HANDLED
