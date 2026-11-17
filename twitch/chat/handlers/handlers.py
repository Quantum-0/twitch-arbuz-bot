import asyncio
import logging
import random
import re
from abc import abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from enum import Enum, auto
from time import time

from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TwitchUserSettings, User
from schemas.twitch import ChatMessageWebhookEventSchema
from twitch.chat.command_manager import text_without_reply_prefix
from twitch.state_manager import SMParam, StateManager
from utils.misc import call_with_delay

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class HandlerResult(Enum):
    SKIPED = auto()
    HANDLED = auto()
    HANDLED_AND_CONTINUE = auto()


class CommonMessagesHandler:
    @abstractmethod
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        raise NotImplementedError

    async def is_candidate(self, message: ChatMessageWebhookEventSchema) -> bool:
        """Дешёвая проверка ДО загрузки стримера из БД: может ли handler
        среагировать на это сообщение. Вызывается по каждому сообщению, поэтому
        здесь нельзя ходить в БД (Redis/текст — можно).

        Контракт: результат — надмножество реальных условий ``handle`` (False
        только когда handler гарантированно вернёт SKIPED). Дефолт True —
        консервативно: handler без собственного предиката не отключает
        оптимизацию, а лишь оставляет за собой полный путь с БД.
        """
        return True

    def __init__(
        self,
        sm: StateManager,
        send_message: Callable[..., Awaitable[None]],
        db_session_factory: Callable[[], AsyncSession] | None = None,
    ):
        self._state_manager = sm
        self.send_response = send_message
        self._db_session_factory = db_session_factory

    def _get_db_session_factory(self) -> Callable[[], AsyncSession]:
        if self._db_session_factory is None:
            raise RuntimeError("DB session factory was not configured for handler")
        return self._db_session_factory

    @asynccontextmanager
    async def db_session(self) -> AsyncIterator[AsyncSession]:
        async with self._get_db_session_factory()() as session:
            yield session

    @abstractmethod
    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema) -> HandlerResult:
        raise NotImplementedError


class PyramidHandler(CommonMessagesHandler):
    COMMAND_NAME = "pyramid_handler"

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return False  # streamer_settings.enable_pyramid or streamer_settings.enable_pyramid_breaker

    async def is_candidate(self, message: ChatMessageWebhookEventSchema) -> bool:
        # Хендлер выключен хардкодом (is_enabled → False) — кандидатом быть не может.
        return False

    def _single_emote(self, message: ChatMessageWebhookEventSchema) -> tuple[str | None, int]:
        """Единственный эмод в сообщении → (emote, количество); текст сообщения очищается от эмода."""
        if not (isinstance(message.emotes, dict) and len(message.emotes.keys()) == 1):
            return None, 0
        emote = list(message.emotes.keys())[0]
        emote_count = len(list(message.emotes.items())[0][1])
        ranges = [(int(x["start_position"]), int(x["end_position"])) for x in list(message.emotes.items())[0][1]]
        cutted = 0
        for rng in ranges:
            message.message.text = message.message.text[: rng[0] - cutted] + message.message.text[1 + rng[1] - cutted :]
            cutted += 1 + rng[1] - rng[0]
        message.message.text = message.message.text.strip()
        if message.message.text != "":
            return None, 0
        return emote, emote_count

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema) -> HandlerResult:
        user = message.chatter_user_name
        emote, emote_count = self._single_emote(message)

        # Load previous state
        channel = streamer.login_name
        state_user = await self._state_manager.get_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER)
        state_emote = await self._state_manager.get_state(
            channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE
        )
        state_height = await self._state_manager.get_state(
            channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT
        )
        state_dir = await self._state_manager.get_state(
            channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION
        )
        state_exists = bool(state_user) or bool(state_emote) or bool(state_height) or bool(state_dir)

        if state_exists and (not emote or emote != state_emote):
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION)
            if state_height >= 3 or state_dir == "DOWN":
                await self.send_response(
                    chat=streamer,
                    message=f"@{user} поломал пирамидку @{state_user}. Ехехе",
                )
                return HandlerResult.HANDLED
            return HandlerResult.HANDLED_AND_CONTINUE

        if not state_exists and emote and emote_count == 1:
            # Начало пирамидки
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.USER,
                value=user,
            )
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.EMOTE,
                value=emote,
            )
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.HEIGHT,
                value=emote_count,
            )
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.DIRECTION,
                value="UP",
            )
            return HandlerResult.HANDLED_AND_CONTINUE
        if emote == state_emote and (emote_count == state_height + 1) and state_dir == "UP":
            # +1 вверх
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.USER,
                value=user,
            )
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE, value=emote)
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.HEIGHT,
                value=emote_count,
            )
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION, value="UP")
            return HandlerResult.HANDLED_AND_CONTINUE
        if emote == state_emote and (emote_count == state_height - 1) and state_dir == "UP":
            # развернулись вниз
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.USER,
                value=user,
            )
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE, value=emote)
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.HEIGHT,
                value=emote_count,
            )
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.DIRECTION,
                value="DOWN",
            )
            return HandlerResult.HANDLED_AND_CONTINUE
        if emote == state_emote and (emote_count == state_height - 1) and emote_count > 1 and state_dir == "DOWN":
            # -1
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.USER,
                value=user,
            )
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE, value=emote)
            await self._state_manager.set_state(
                channel=channel,
                command=self.COMMAND_NAME,
                param=SMParam.HEIGHT,
                value=emote_count,
            )
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION, value="DOWN")
            return HandlerResult.HANDLED_AND_CONTINUE
        if emote == state_emote and (emote_count == 1) and state_dir == "DOWN":
            # закончили пирамидку
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION)
            await self.send_response(chat=streamer, message=f"@{user} достроил пирамидку! Молодец!")
            return HandlerResult.HANDLED

        return HandlerResult.SKIPED


class MessagesHandlerManager:
    def __init__(
        self,
        storage: StateManager,
        send_message: Callable[..., Awaitable[None]],
        db_session_factory: Callable[[], AsyncSession] | None = None,
    ):
        self.handlers: list[CommonMessagesHandler] = []
        self._sm = storage
        self._send_message = send_message
        self._db_session_factory = db_session_factory

    def register(self, command: type[CommonMessagesHandler]):
        self.handlers.append(command(self._sm, self._send_message, self._db_session_factory))

    async def is_any_candidate(self, message: ChatMessageWebhookEventSchema) -> bool:
        """Префикс-фильтр до загрузки стримера из БД: хотя бы один handler
        может среагировать на сообщение (см. ``CommonMessagesHandler.is_candidate``)."""
        for handler in self.handlers:
            if await handler.is_candidate(message):
                return True
        return False

    @tracer.start_as_current_span("ChatBot: Messages Handler Manager: Handle")
    async def handle(
        self,
        user_settings: TwitchUserSettings,
        streamer: User,
        message: ChatMessageWebhookEventSchema,
    ):
        logger.debug(f"Handling message with {self.__class__.__name__}")
        if not user_settings.allow_shared_chat and message.source_broadcaster_user_id:
            logger.debug(
                f"Skip message because of common chat. Source: {message.source_broadcaster_user_login}, Broadcaster: {message.broadcaster_user_login}"
            )
            return
        for handler in self.handlers:
            if not handler.is_enabled(user_settings):
                continue
            logger.info(f"Handling message with {handler.__class__.__name__}")
            res: HandlerResult = await handler.handle(streamer, message)
            if res == HandlerResult.HANDLED:
                return


class UnlurkHandler(CommonMessagesHandler):
    COMMAND_NAME = "lurk"
    UNLURK_AFTER = 300

    def __init__(
        self,
        sm: StateManager,
        send_message: Callable[..., Awaitable[None]],
        db_session_factory: Callable[[], AsyncSession] | None = None,
    ):
        from container_runtime import get_container

        self.chat_bot = get_container().chat_bot()
        super().__init__(sm, send_message, db_session_factory)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True or streamer_settings.enable_lurk

    async def is_candidate(self, message: ChatMessageWebhookEventSchema) -> bool:
        # Триггер — любое сообщение от юзера в лурке: без лурк-стейта handler
        # гарантированно SKIPED. Ключ стейта — display name (см. handle).
        if any(x in message.message.text for x in ("!lurk", "!unlurk", "!лурк", "!анлурк")):
            return False
        state = await self._state_manager.get_state(
            channel=message.broadcaster_user_login,
            user=message.chatter_user_name.lower(),
            command=self.COMMAND_NAME,
        )
        return state is not None

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema) -> HandlerResult:
        if any(x in message.message.text for x in ("!lurk", "!unlurk", "!лурк", "!анлурк")):
            return HandlerResult.SKIPED

        display_name = message.chatter_user_name
        # Лурк-стейт ключуется по display name — так его пишет LurkCommand
        # (SimpleCDCommand передаёт в _handle только chatter_user_name), и
        # активность юзеров UserListManager хранит тоже по display name.
        # Чтение по логину не находило стейт у юзеров с display ≠ логин.
        state_user = display_name.lower()

        previous_state: float = await self._state_manager.get_state(
            channel=streamer.login_name, user=state_user, command=self.COMMAND_NAME
        )
        if previous_state is not None and time() - previous_state > self.UNLURK_AFTER:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                user=state_user,
                command=self.COMMAND_NAME,
                value=None,
            )
            last_active = await self.chat_bot.get_user_last_active(streamer.login_name, display_name)
            if last_active is not None and time() - last_active > self.UNLURK_AFTER:
                await self.send_response(chat=streamer, message=f"@{display_name}, с возвращением из лурка!")
            return HandlerResult.HANDLED_AND_CONTINUE
        return HandlerResult.SKIPED


class HelloHandler(CommonMessagesHandler):
    HELLO_WORDS = ("привет", "дарова", "здравствуй", "кваствуй", "здорова")

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def is_candidate(self, message: ChatMessageWebhookEventSchema) -> bool:
        # Приветствие + адресация боту (тег/реплай) или «всем» (шанс проверяется в handle).
        text = message.message.text.lower()
        if not any(hello_word in text for hello_word in self.HELLO_WORDS):
            return False
        return (
            "@quantum075bot" in text
            or "всем" in text
            or (message.reply is not None and message.reply.parent_user_name == "quantum075bot")
        )

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema) -> HandlerResult:
        if message.reply and message.reply.parent_user_name == "quantum075bot":
            message.message.text = "@quantum075bot " + message.message.text
        if any(hello_word in message.message.text.lower() for hello_word in self.HELLO_WORDS) and (
            "@quantum075bot" in message.message.text.lower()
            or ("всем" in message.message.text.lower() and random.random() < 0.25)
        ):
            replies = [
                f"@{message.chatter_user_name}, и тебе привет!",
                f"@{message.chatter_user_name}, здравствуй-здравствуй!",
                f"@{message.chatter_user_name}, дарова! >w<",
            ]
            if streamer.login_name in ("anna_toad", "toad_anna"):
                replies = [
                    f"@{message.chatter_user_name}, кваствуй! >w<",
                    f"Кваствуй, @{message.chatter_user_name}! <3",
                ]
            if streamer.login_name == "glumarkoj":
                replies = [
                    f"@{message.chatter_user_name}, здорова, брат!",
                ]
            await self.send_response(chat=streamer, message=random.choice(replies))
            return HandlerResult.HANDLED
        return HandlerResult.SKIPED


class IAmBotHandler(CommonMessagesHandler):
    _RE_BOT_QUESTION = re.compile(r"@quantum075bot .{0,5}бот\?")
    _RE_BOTS_PLUS = re.compile(r"(кто )?боты? (- )?(плюс|плюсик|плюсики|плюсаните|\+)( в ча[тч])")

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def is_candidate(self, message: ChatMessageWebhookEventSchema) -> bool:
        # Триггеры анкорные (re.match — от начала строки), а CommandsManager до
        # хендлеров срезает reply-префикс «@имя » — проверяем оба варианта текста.
        text = message.message.text.lower()
        stripped = text_without_reply_prefix(message).lower()
        if self._RE_BOT_QUESTION.match(text) or self._RE_BOTS_PLUS.match(text):
            return True
        if self._RE_BOT_QUESTION.match(stripped) or self._RE_BOTS_PLUS.match(stripped):
            return True
        # HelloHandler (раньше в цепочке) дописывает «@quantum075bot » к реплаям
        # на бота — после этого может сматчиться _RE_BOT_QUESTION.
        return message.reply is not None and message.reply.parent_user_name == "quantum075bot" and "бот?" in text

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema) -> HandlerResult:
        if self._RE_BOT_QUESTION.match(message.message.text.lower()):
            if random.random() < 0.1:
                asyncio.create_task(
                    call_with_delay(
                        0.5,
                        self.send_response(chat=streamer, message="Конеяно я бот!"),  # опечатка намеренно
                    )
                )
                asyncio.create_task(
                    call_with_delay(
                        2.5,
                        self.send_response(chat=streamer, message="конечно* 👀"),
                    )
                )
            else:
                replies = [
                    f"@{message.chatter_user_name}, конечно я бот! Какие могут быть сомнения?",
                    f"@{message.chatter_user_name}, да, я бот, и я горжусь этим!",
                    f"@{message.chatter_user_name}, почему ты так думаешь?",
                    f"@{message.chatter_user_name}, нет, я настоящий живой человек, @Quantum075 держит меня в подвале и заставляет отвечать на сообщения Т_Т",
                    f"@{message.chatter_user_name}, MrDestructoid !",
                ]
                await self.send_response(chat=streamer, message=random.choice(replies))
            return HandlerResult.HANDLED

        if self._RE_BOTS_PLUS.match(message.message.text.lower()):
            await self.send_response(chat=streamer, message="+")
            return HandlerResult.HANDLED

        return HandlerResult.SKIPED
