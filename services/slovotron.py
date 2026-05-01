from collections.abc import Callable
from typing import Any

from dependency_injector.wiring import inject, Provide
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from routers.schemas import SlovotronWebhookSchema, SlovotronNewWebhookSchema, SlovotronWinWebhookSchema, \
    SlovotronTipWebhookSchema, SlovotronEvent
from twitch.chat.bot import ChatBot
import sqlalchemy as sa


class SlovotronService:
    def __init__(self, db_session_factory: Callable[[], AsyncSession], chat_bot: ChatBot):
        self._client = None
        self._db_session_factory = db_session_factory
        self._webhook_type_adapter = TypeAdapter(SlovotronWebhookSchema)
        self._chat_bot = chat_bot

    async def handle_webhook(self, payload: SlovotronWebhookSchema | dict[str, Any]):
        # Парсим данные
        payload_model: SlovotronNewWebhookSchema | SlovotronWinWebhookSchema | SlovotronTipWebhookSchema
        if isinstance(payload, SlovotronNewWebhookSchema | SlovotronWinWebhookSchema | SlovotronTipWebhookSchema):
            payload_model = payload
        elif isinstance(payload, dict):
            try:
                payload_model = self._webhook_type_adapter.validate_python(payload)
            except ValidationError:
                raise
        else:
            raise ValueError

        # Обрабатываем
        match payload_model.event:
            case SlovotronEvent.GAME_NEW:
                # print(f"Новая игра: {payload_model.data.secret_word}")
                await self.handle_game_new(payload_model)  # type: ignore
            case SlovotronEvent.GAME_WIN:
                # print(f"Победа: {payload_model.data.winner}")
                await self.handle_game_win(payload_model)  # type: ignore
            case SlovotronEvent.GAME_TIP:
                # print(f"Подсказка: {payload_model.data.tip_word}")
                await self.handle_game_tip(payload_model)  # type: ignore

    async def handle_game_new(self, payload: SlovotronNewWebhookSchema):
        pass

    async def handle_game_win(self, payload: SlovotronWinWebhookSchema):
        async with self._db_session_factory() as session:
            user: User = (await session.execute(  # type: ignore
                sa.select(User).where(User.login_name == payload.channel)
            )).scalar_one_or_none()
        await self._chat_bot.send_message(user, f"@{payload.data.winner.display_name} угадывает слово {payload.data.winning_word}! Поздравляем!")

    async def handle_game_tip(self, payload: SlovotronTipWebhookSchema):
        pass