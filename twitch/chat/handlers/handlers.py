import asyncio
import logging
import random
import re
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from enum import Enum, auto
from time import time

from twitchAPI.chat import ChatMessage

from database.models import TwitchUserSettings, User
from routers.schemas import ChatMessageWebhookEventSchema
from twitch.state_manager import SMParam, StateManager

logger = logging.getLogger(__name__)


class HandlerResult(Enum):
    SKIPED = auto()
    HANDLED = auto()
    HANDLED_AND_CONTINUE = auto()


class CommonMessagesHandler:
    @abstractmethod
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        raise NotImplementedError

    def __init__(self, sm: StateManager, send_message: Callable[..., Awaitable[None]]):
        self._state_manager = sm
        self.send_response = send_message

    @abstractmethod
    async def handle(
        self, streamer: User, message: ChatMessageWebhookEventSchema
    ) -> HandlerResult:
        raise NotImplementedError


class PyramidHandler(CommonMessagesHandler):
    COMMAND_NAME = "pyramid_handler"

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return False  # streamer_settings.enable_pyramid or streamer_settings.enable_pyramid_breaker

    async def handle(
        self, streamer: User, message: ChatMessageWebhookEventSchema
    ) -> HandlerResult:
        # Check if pyramid part
        user = message.chatter_user_name
        if isinstance(message.emotes, dict) and len(message.emotes.keys()) == 1:
            emote = list(message.emotes.keys())[0]
            emote_count = len(list(message.emotes.items())[0][1])
            ranges = [
                (int(x["start_position"]), int(x["end_position"]))
                for x in list(message.emotes.items())[0][1]
            ]
            cutted = 0
            for rng in ranges:
                message.message.text = (
                    message.message.text[: rng[0] - cutted]
                    + message.message.text[1 + rng[1] - cutted :]
                )
                cutted += 1 + rng[1] - rng[0]
            message.message.text = message.message.text.strip()
            if message.message.text != "":
                emote = None
                emote_count = 0
        else:
            emote = None
            emote_count = 0

        # Load previous state
        channel = streamer.login_name
        state_user = await self._state_manager.get_state(
            channel=channel, command=self.COMMAND_NAME, param=SMParam.USER
        )
        state_emote = await self._state_manager.get_state(
            channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE
        )
        state_height = await self._state_manager.get_state(
            channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT
        )
        state_dir = await self._state_manager.get_state(
            channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION
        )
        state_exists = (
            bool(state_user)
            or bool(state_emote)
            or bool(state_height)
            or bool(state_dir)
        )

        if state_exists and (not emote or emote != state_emote):
            await self._state_manager.del_state(
                channel=channel, command=self.COMMAND_NAME, param=SMParam.USER
            )
            await self._state_manager.del_state(
                channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE
            )
            await self._state_manager.del_state(
                channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT
            )
            await self._state_manager.del_state(
                channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION
            )
            if state_height >= 3 or state_dir == "DOWN":
                await self.send_response(
                    chat=streamer,
                    message=f"@{user} поломал пирамидку @{state_user}. Ехехе",
                )
                return HandlerResult.HANDLED
            else:
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
        if (
            emote == state_emote
            and (emote_count == state_height + 1)
            and state_dir == "UP"
        ):
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
        elif (
            emote == state_emote
            and (emote_count == state_height - 1)
            and state_dir == "UP"
        ):
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
        elif (
            emote == state_emote
            and (emote_count == state_height - 1)
            and emote_count > 1
            and state_dir == "DOWN"
        ):
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
        elif emote == state_emote and (emote_count == 1) and state_dir == "DOWN":
            # закончили пирамидку
            await self._state_manager.del_state(
                channel=channel, command=self.COMMAND_NAME, param=SMParam.USER
            )
            await self._state_manager.del_state(
                channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE
            )
            await self._state_manager.del_state(
                channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT
            )
            await self._state_manager.del_state(
                channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION
            )
            await self.send_response(
                chat=streamer, message=f"@{user} достроил пирамидку! Молодец!"
            )
            return HandlerResult.HANDLED

        return HandlerResult.SKIPED


class MessagesHandlerManager:
    def __init__(
        self, storage: StateManager, send_message: Callable[..., Awaitable[None]]
    ):
        self.handlers: list[CommonMessagesHandler] = []
        self._sm = storage
        self._send_message = send_message

    def register(self, command: type[CommonMessagesHandler]):
        self.handlers.append(command(self._sm, self._send_message))

    async def handle(
        self,
        user_settings: TwitchUserSettings,
        streamer: User,
        message: ChatMessageWebhookEventSchema,
    ):
        logger.debug(f"Handling message with {self.__class__.__name__}")
        for handler in self.handlers:
            if not handler.is_enabled(user_settings):
                continue
            res: HandlerResult = await handler.handle(streamer, message)
            if res == HandlerResult.HANDLED:
                return


class UnlurkHandler(CommonMessagesHandler):
    COMMAND_NAME = "lurk"
    UNLURK_AFTER = 300

    def __init__(self, sm: StateManager, send_message: Callable[..., Awaitable[None]]):
        from container_runtime import get_container

        self.chat_bot = get_container().chat_bot()
        super().__init__(sm, send_message)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True or streamer_settings.enable_lurk

    async def handle(
        self, streamer: User, message: ChatMessageWebhookEventSchema
    ) -> HandlerResult:
        if any(
            x in message.message.text for x in ("!lurk", "!unlurk", "!лурк", "!анлурк")
        ):
            return HandlerResult.SKIPED

        user = message.chatter_user_login

        previous_state: float = await self._state_manager.get_state(
            channel=streamer.login_name, user=user, command=self.COMMAND_NAME
        )
        if previous_state is not None and time() - previous_state > self.UNLURK_AFTER:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                user=user,
                command=self.COMMAND_NAME,
                value=None,
            )
            last_active = await self.chat_bot.get_user_last_active(
                streamer.login_name, user
            )
            if time() - last_active > self.UNLURK_AFTER:
                await self.send_response(
                    chat=streamer, message=f"@{user}, с возвращением из лурка!"
                )
            return HandlerResult.HANDLED_AND_CONTINUE
        return HandlerResult.SKIPED


class HelloHandler(CommonMessagesHandler):
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def handle(
        self, streamer: User, message: ChatMessageWebhookEventSchema
    ) -> HandlerResult:
        if message.reply and message.reply.parent_user_name == "quantum075bot":
            message.message.text = "@quantum075bot " + message.message.text
        if any(
            hello_word in message.message.text.lower()
            for hello_word in {"привет", "дарова", "здравствуй", "кваствуй", "здорова"}
        ) and ("@quantum075bot" in message.message.text.lower() or ("всем" in message.message.text.lower() and random.random() < 0.25)):
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
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def handle(
        self, streamer: User, message: ChatMessageWebhookEventSchema
    ) -> HandlerResult:
        if re.match(r"@quantum075bot .{0,5}бот\?", message.message.text.lower()):
            if random.random() < 0.1:
                await asyncio.sleep(0.5)
                await self.send_response(chat=streamer, message=f"Конеяно я бот!") # опечатка намеренно
                await asyncio.sleep(2)
                await self.send_response(chat=streamer, message=f"конечно* 👀")
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

        if re.match(
            r"(кто )?боты? (- )?(плюс|плюсик|плюсики|плюсаните|\+)( в ча[тч])",
            message.message.text.lower(),
        ):
            await self.send_response(chat=streamer, message="+")
            return HandlerResult.HANDLED

        return HandlerResult.SKIPED


# TODO: "спасибо", "пожалуйста", "молодец", "умничка", "пасиба", "спс", "благодарю"
