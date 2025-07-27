import asyncio
import logging.config
import random

from sqlalchemy.orm import selectinload
from twitchAPI import Chat
from twitchAPI.chat import ChatMessage
from twitchAPI.types import ChatEvent

from database.database import AsyncSessionLocal
from database.models import User, TwitchUserSettings
from twitch.command import BiteCommand, LickCommand, BananaCommand, BoopCommand
from twitch.command_manager import CommandsManager
from twitch.state_manager import get_state_manager
from twitch.twitch import Twitch
from utils.logging_conf import LOGGING_CONFIG
from utils.singleton import singleton
import sqlalchemy as sa

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


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

        async def on_message(msg: ChatMessage):
            logger.debug(f"[Wrapper] Got message `{msg}`")
            asyncio.run_coroutine_threadsafe(self.on_message(msg), event_loop)

        self._command_manager = CommandsManager(get_state_manager(), self.send_message)
        self._command_manager.register(BiteCommand)
        self._command_manager.register(LickCommand)
        self._command_manager.register(BananaCommand)
        self._command_manager.register(BoopCommand)

        chat.register_event(ChatEvent.MESSAGE, on_message)
        logger.debug("On_message handler registered")
        chat.start()
        self._chat = chat
        logger.info("Chat bot started!")

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
        logger.info(f"Sending message `{message}` to channel `{chat}`")
        await self._chat.send_message(chat.lower(), message)

    async def on_message(self, message):
        channel = message.room.name  # Имя канала

        logger.debug(f"Got message `{message.text}` from channel `{channel}`")

        async with AsyncSessionLocal() as session:
            result = await session.execute(sa.select(User).options(selectinload(User.settings)).filter_by(login_name=channel.lower()))
            user = result.scalar_one_or_none()
            if not user:
                return
            if not user.in_beta_test:
                return

            user_settings: TwitchUserSettings = user.settings
            await self._command_manager.handle(user_settings, channel, message)
            #if any(message.text.startswith(x) for x in ['!hug', '!обнять', '!обнимашки']) and user_settings.enable_hug:
            #    await cmd_hug_handler(self, channel, message)
            # if any(message.text.startswith(x) for x in ['!якто', '!ктоя', '!whoami']):
            #     await cmd_whoami_handler(self, channel, message)
            # if any(message.text.startswith(x) for x in ['!horny', '!хорни']):
            #     await cmd_horny_handler(self, channel, message)

    async def update_bot_channels(self):
        if not self._chat:
            return
        async with AsyncSessionLocal() as session:
            users_result = await session.execute(
                sa.select(User)
                .join(TwitchUserSettings)
                .filter(
                    sa.or_(
                        TwitchUserSettings.enable_chat_bot == True,
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
            if desired_channels - current_channels:
                logger.info(f"Joined to channels: {desired_channels - current_channels}")
            # Покидаем ненужные каналы
            for channel in current_channels - desired_channels:
                await self._chat.leave_room(channel)
                self._joined_channels.remove(channel)
            if current_channels - desired_channels:
                logger.info(f"Left channels: {current_channels - desired_channels}")
