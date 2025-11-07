import asyncio
import logging.config
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import time

import sqlalchemy as sa
from sqlalchemy.orm import selectinload
from twitchAPI.chat import Chat

from database.database import AsyncSessionLocal
from database.models import TwitchUserSettings, User
from exceptions import NotInBetaTest, UserNotFoundInDatabase
from routers.schemas import ChatMessageWebhookEventSchema
from twitch.chat.command_manager import CommandsManager
from twitch.chat.commands import *
from twitch.chat.handlers.handlers import (
    HelloHandler,
    IAmBotHandler,
    MessagesHandlerManager,
    PyramidHandler,
    UnlurkHandler,
)
from twitch.client.twitch import Twitch
from twitch.state_manager import get_state_manager
from twitch.user_list_manager import UserListManager
from twitch.utils import delay_to_seconds
from utils.logging_conf import LOGGING_CONFIG
from utils.singleton import singleton

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


@singleton
class ChatBot:
    _chat: Chat = None  # type: ignore
    _joined_channels: list[str] = []
    _main_event_loop: asyncio.AbstractEventLoop

    def __init__(self) -> None:
        self._user_list_manager = UserListManager()
        self._handler_manager: MessagesHandlerManager = MessagesHandlerManager(
            get_state_manager(), self.send_message
        )
        self._command_manager = CommandsManager(get_state_manager(), self.send_message)
        self._twitch: Twitch = None  # type: ignore

    async def startup(self, twitch: Twitch):
        chat = await twitch.build_chat_client()
        self._twitch = twitch

        async def on_message(msg: ChatMessageWebhookEventSchema):
            # asyncio.run_coroutine_threadsafe(self.on_message(msg), event_loop)
            await self.on_message(msg)  # можно будет вернуть когда уйдём от IRC

        self._handler_manager.register(PyramidHandler)
        self._handler_manager.register(UnlurkHandler)
        self._handler_manager.register(HelloHandler)
        self._handler_manager.register(IAmBotHandler)
        self._command_manager.register(CmdlistCommand)
        self._command_manager.register(BiteCommand)
        self._command_manager.register(BushCommand)
        self._command_manager.register(LickCommand)
        self._command_manager.register(BananaCommand)
        self._command_manager.register(TailCommand)
        self._command_manager.register(HornyGoodCommand)
        self._command_manager.register(BoopCommand)
        self._command_manager.register(PatCommand)
        self._command_manager.register(HugCommand)
        self._command_manager.register(LurkCommand)
        self._command_manager.register(PantsCommand)

        # chat.register_event(ChatEvent.MESSAGE, on_message)
        logger.debug("On_message handler registered")
        # chat.start()
        self._chat = chat
        logger.info("Chat bot started!")

    async def send_message(self, chat: User, message: str) -> None:
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

        await self._twitch.send_chat_message(
            stream_channel=chat, message=message, reply_parent_message_id=None
        )

    @staticmethod
    @asynccontextmanager
    async def _get_user_with_settings(channel_name: str) -> AsyncIterator[User]:
        channel_name = channel_name.lower()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                sa.select(User)
                .options(selectinload(User.settings))
                .filter_by(login_name=channel_name)
            )
            user = result.scalar_one_or_none()
            if not user:
                logger.error(f"User {channel_name} not found")
                raise UserNotFoundInDatabase
            if not user.in_beta_test:
                logger.error(f"User {channel_name} not in beta test")
                raise NotInBetaTest
            yield user

    async def on_message(self, message: ChatMessageWebhookEventSchema):
        channel = message.broadcaster_user_login

        logger.debug(f"Got message `{message.message.text}` from channel `{channel}`")

        async with self._get_user_with_settings(channel) as user:
            user_settings: TwitchUserSettings = user.settings
            await self._user_list_manager.handle(channel, message)
            await self._command_manager.handle(user_settings, user, message)
            await self._handler_manager.handle(user_settings, user, message)

    async def update_bot_channels(self):
        logger.info("Updating bot channels")
        async with AsyncSessionLocal() as session:
            users_result = await session.execute(
                sa.select(User)
                .join(TwitchUserSettings)
                .filter(TwitchUserSettings.enable_chat_bot == True)
            )
            users = users_result.scalars().all()
        desired_channels: set[User] = {user for user in users}

        # Получаем текущие подписки
        subs = await self._twitch.get_subscriptions()
        current_channels = {
            sub.condition.get("broadcaster_user_id")
            for sub in subs.data
            if sub.type == "channel.chat.message"
        }

        # Присоединяемся к новым каналам
        async for channel, success, response in self._twitch.subscribe_chat_messages(
            *(
                channel
                for channel in desired_channels
                if channel.twitch_id not in current_channels
            )
        ):
            if success:
                logger.info(
                    f"Subscribed to EventSub chat messages for {channel.login_name}"
                )
            else:
                logger.error(
                    f"Error to join user's channel as chat bot. User: `{channel.login_name}`"
                )

        # Отписываемся от ненужных каналов
        for sub in subs.data:
            if sub.type == "channel.chat.message" and sub.condition.get(
                "broadcaster_user_id"
            ) not in {channel.twitch_id for channel in desired_channels}:
                await self._twitch.unsubscribe_event_sub(sub.id)
                logger.info(f"Unsubscribed from {sub.condition}")

    async def get_commands(self, user: User) -> list[tuple[str, str, str]]:
        return await self._command_manager.get_commands_of_user(user)

    async def get_last_active_users(self, user: User | str) -> list[tuple[str, str]]:
        result = []
        dt = time()
        for name, last_active in self._user_list_manager.get_active_users(
            user.login_name if isinstance(user, User) else user
        ):
            result.append((name, delay_to_seconds(dt - last_active) + " назад"))
        return result

    async def get_user_last_active(self, channel: str, user: str) -> float:
        return self._user_list_manager.get_last_active(channel, user)

    async def get_random_active_user(self, channel: User | str, max_period_sec: float = 30*60) -> str:
        users = self._user_list_manager.get_active_users(
            channel.login_name if isinstance(channel, User) else channel,
            timeout=max_period_sec,
        )
        logger.info(f"active users: {users}")
        return random.choice(users)[0]