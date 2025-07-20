import asyncio
import random

from twitchAPI import Chat
from twitchAPI.types import ChatEvent

from database.database import AsyncSessionLocal
from database.models import User, TwitchUserSettings
from twitch.twitch import Twitch
from utils.singleton import singleton
import sqlalchemy as sa


@singleton
class ChatBot:
    _chat: Chat = None
    _joined_channels: list[str] = []
    _main_event_loop: asyncio.AbstractEventLoop

    def __init__(self):
        pass

    async def startup(self, twitch: Twitch, event_loop: asyncio.AbstractEventLoop):
        self._main_event_loop = event_loop
        chat = await twitch.build_chat_client()
        chat.register_event(ChatEvent.MESSAGE, self._on_message_wrapper)
        chat.start()
        self._chat = chat

    async def send_message(self, chat: User | str, message: str) -> None:
        """
        Send message to twitch chat.

        :param chat: Модель пользователя в БД или логин твича.
        :param message: Текст сообщения.
        """
        if isinstance(chat, User):
            chat = chat.login_name
        elif not isinstance(chat, str):
            raise ValueError
        await self._chat.send_message(chat, message)

    async def _on_message_wrapper(self, message):
        asyncio.run_coroutine_threadsafe(self.on_message(message), self._main_event_loop)

    async def on_message(self, message):
        channel = message.room.name  # Имя канала

        async with AsyncSessionLocal() as session:
            result = await session.execute(sa.select(User).filter_by(login_name=channel.lower()))
            user = result.scalar_one_or_none()
            if user:
                if message.text.startswith('!help') and user.settings.enable_help:
                    await self.send_message(channel, 'Доступные команды: !help, !random, !fruit')
                elif message.text.startswith('!random') and user.settings.enable_random:
                    number = random.randint(0, 10)
                    await self.send_message(channel, f'Случайное число: {number}')
                elif message.text.startswith('!fruit') and user.settings.enable_fruit:
                    fruits = ['яблоко', 'груша', 'банан']
                    fruit = random.choice(fruits)
                    await self.send_message(channel, f'Случайный фрукт: {fruit}')

    async def update_bot_channels(self):
        if not self._chat:
            return
        async with AsyncSessionLocal() as session:
            users_result = await session.execute(
                sa.select(User)
                .join(TwitchUserSettings)
                .filter(
                    sa.or_(
                        TwitchUserSettings.enable_help == True,
                        TwitchUserSettings.enable_random == True,
                        TwitchUserSettings.enable_fruit == True,
                    )
                )
            )

            users = users_result.scalars().all()
            desired_channels = {user.login_name.lower() for user in users}
            current_channels = {ch.lower() for ch in self._joined_channels}

            # Присоединяемся к новым каналам
            for channel in desired_channels - current_channels:
                await self._chat.join_room(channel)
                self._joined_channels.append(channel)
            # Покидаем ненужные каналы
            for channel in current_channels - desired_channels:
                await self._chat.leave_room(channel)
                self._joined_channels.remove(channel)