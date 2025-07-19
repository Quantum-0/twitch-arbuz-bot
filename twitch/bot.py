import random

from twitchAPI import Chat
from twitchAPI.types import ChatEvent

from database.database import SessionLocal
from database.models import User, TwitchUserSettings
from twitch.twitch import Twitch
from utils.singleton import singleton


@singleton
class ChatBot:
    _chat: Chat = None
    _joined_channels: list[str] = []

    def __init__(self):
        pass

    async def startup(self):
        chat = await Twitch().build_chat_client()
        chat.register_event(ChatEvent.MESSAGE, self.on_message)
        chat.start()
        self._chat = chat

    @staticmethod
    async def on_message(message):
        channel = message.room.name  # Имя канала

        with SessionLocal() as session:
            user = session.query(User).filter_by(login_name=channel.lower()).first()
            if user:
                if message.text.startswith('!help') and user.settings.enable_help:
                    await ChatBot()._chat.send_message(channel, 'Доступные команды: !help, !random, !fruit')
                elif message.text.startswith('!random') and user.settings.enable_random:
                    number = random.randint(0, 10)
                    await ChatBot()._chat.send_message(channel, f'Случайное число: {number}')
                elif message.text.startswith('!fruit') and user.settings.enable_fruit:
                    fruits = ['яблоко', 'груша', 'банан']
                    fruit = random.choice(fruits)
                    await ChatBot()._chat.send_message(channel, f'Случайный фрукт: {fruit}')

    async def update_bot_channels(self):
        if not self._chat:
            return
        with SessionLocal() as session:
            users = (
                session.query(User)
                .join(TwitchUserSettings)
                .filter(
                    (TwitchUserSettings.enable_help == True) |
                    (TwitchUserSettings.enable_random == True) |
                    (TwitchUserSettings.enable_fruit == True)
                )
                .all()
            )
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