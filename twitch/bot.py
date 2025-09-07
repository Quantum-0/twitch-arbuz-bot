import asyncio
import logging.config
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import time

from sqlalchemy.orm import selectinload
from twitchAPI.chat import ChatMessage, Chat
from twitchAPI.type import ChatEvent

from database.database import AsyncSessionLocal
from database.models import User, TwitchUserSettings
from exceptions import UserNotFoundInDatabase, NotInBetaTest
from twitch.command import BiteCommand, LickCommand, BananaCommand, BoopCommand, CmdlistCommand, PatCommand, HugCommand, \
    LurkCommand, PantsCommand
from twitch.command_manager import CommandsManager
from twitch.handlers import MessagesHandlerManager, PyramidHandler, UnlurkHandler, HelloHandler, IAmBotHandler
from twitch.state_manager import get_state_manager
from twitch.twitch import Twitch
from twitch.user_list_manager import UserListManager
from twitch.utils import delay_to_seconds
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
        self._user_list_manager = UserListManager()
        self._handler_manager: MessagesHandlerManager = MessagesHandlerManager(get_state_manager(), self.send_message)
        self._command_manager = CommandsManager(get_state_manager(), self.send_message)
        self._twitch: Twitch = None  # type: ignore

    async def startup(self, twitch: Twitch, event_loop: asyncio.AbstractEventLoop):
        self._main_event_loop = event_loop
        chat = await twitch.build_chat_client()
        self._twitch = twitch

        async def on_message(msg: ChatMessage):
            asyncio.run_coroutine_threadsafe(self.on_message(msg), event_loop)
            # await self.on_message(msg)  # можно будет вернуть когда уйдём от IRC

        self._handler_manager.register(PyramidHandler)
        self._handler_manager.register(UnlurkHandler)
        self._handler_manager.register(HelloHandler)
        self._handler_manager.register(IAmBotHandler)
        self._command_manager.register(CmdlistCommand)
        self._command_manager.register(BiteCommand)
        self._command_manager.register(LickCommand)
        self._command_manager.register(BananaCommand)
        self._command_manager.register(BoopCommand)
        self._command_manager.register(PatCommand)
        self._command_manager.register(HugCommand)
        self._command_manager.register(LurkCommand)
        self._command_manager.register(PantsCommand)

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
        await self.send_message_via_api(chat, message)

    async def send_message_via_irc(self, chat: User | str, message: str) -> None:
        """
        Send message to twitch chat via IRC.

        :param chat: Модель пользователя в БД или логин твича.
        :param message: Текст сообщения.
        """
        if isinstance(chat, User):
            chat = chat.login_name
        elif not isinstance(chat, str):
            raise ValueError
        if message is None or len(message) == 0:
            logger.info(f"No message to send to channel `{chat}`")
            return
        logger.info(f"Sending message `{message}` to channel `{chat}`")
        await self._chat.send_message(chat.lower(), message)

    async def send_message_via_api(self, chat: User, message: str) -> None:
        """
        Send message to twitch chat via API.

        :param chat: Модель пользователя в БД.
        :param message: Текст сообщения.
        """

        await self._twitch.send_chat_message(stream_channel=chat, message=message, reply_parent_message_id=None)

    @staticmethod
    @asynccontextmanager
    async def _get_user_with_settings(channel_name: str) -> AsyncIterator[User]:
        channel_name = channel_name.lower()
        async with AsyncSessionLocal() as session:
            result = await session.execute(sa.select(User).options(selectinload(User.settings)).filter_by(login_name=channel_name))
            user = result.scalar_one_or_none()
            if not user:
                logger.error(f"User {channel_name} not found")
                raise UserNotFoundInDatabase
            if not user.in_beta_test:
                logger.error(f"User {channel_name} not in beta test")
                raise NotInBetaTest
            yield user

    async def on_message(self, message: ChatMessage):
        channel = message.room.name  # Имя канала

        logger.debug(f"Got message `{message.text}` from channel `{channel}`")

        async with self._get_user_with_settings(channel) as user:
            user_settings: TwitchUserSettings = user.settings
            await self._user_list_manager.handle(channel, message)
            await self._command_manager.handle(user_settings, user, message)
            await self._handler_manager.handle(user_settings, user, message)

    # async def update_bot_channels(self, twitch: Twitch):
    #     """
    #     Вместо join/leave в IRC — управление EventSub подписками на channel.chat.message
    #     """
    #     async with AsyncSessionLocal() as session:
    #         users_result = await session.execute(
    #             sa.select(User)
    #             .join(TwitchUserSettings)
    #             .filter(TwitchUserSettings.enable_chat_bot == True)
    #         )
    #         users = users_result.scalars().all()
    #         desired_channels = {user.login_name.lower() for user in users}
    #
    #     # Получаем текущие подписки
    #     subs = await twitch.get_eventsub_subscriptions()
    #     current_channels = {
    #         sub.condition.get("broadcaster_user_id")
    #         for sub in subs
    #         if sub.type == "channel.chat.message"
    #     }
    #
    #     # Присоединяемся к новым каналам
    #     for channel in desired_channels:
    #         user_id = await twitch.get_user_id(channel)
    #         if user_id not in current_channels:
    #             await twitch.subscribe_channel_chat_message(user_id)
    #             logger.info(f"Subscribed to EventSub chat messages for {channel}")
    #
    #     # Отписываемся от ненужных каналов
    #     for sub in subs:
    #         if sub.type == "channel.chat.message" and sub.condition.get(
    #                 "broadcaster_user_login") not in desired_channels:
    #             await twitch.unsubscribe_eventsub(sub.id)
    #             logger.info(f"Unsubscribed from {sub.condition}")

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

    async def get_commands(self, user: User) -> list[tuple[str, str, str]]:
        return await self._command_manager.get_commands_of_user(user)

    async def get_last_active_users(self, user: User | str) -> list[tuple[str, str]]:
        result = []
        dt = time()
        for name, last_active in self._user_list_manager.get_active_users(user.login_name if isinstance(user, User) else user):
            result.append((name, delay_to_seconds(dt - last_active) + " назад"))
        return result

    async def get_user_last_active(self, channel: str, user: str) -> float:
        return self._user_list_manager.get_last_active(channel, user)