import asyncio
import logging
import random
import re
from abc import abstractmethod
from collections.abc import Callable, Awaitable
from enum import auto, Enum
from time import time

from twitchAPI.chat import ChatMessage

from database.models import TwitchUserSettings
from twitch.state_manager import StateManager, SMParam


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
    async def handle(self, channel: str, message: ChatMessage) -> HandlerResult:
        raise NotImplementedError


class PyramidHandler(CommonMessagesHandler):
    COMMAND_NAME = "pyramid_handler"
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pyramid or streamer_settings.enable_pyramid_breaker

    async def handle(self, channel: str, message: ChatMessage) -> HandlerResult:
        # Check if pyramid part
        user = message.user.display_name
        if isinstance(message.emotes, dict) and len(message.emotes.keys()) == 1:
            emote = list(message.emotes.keys())[0]
            emote_count = len(list(message.emotes.items())[0][1])
            ranges = [(int(x["start_position"]), int(x["end_position"])) for x in list(message.emotes.items())[0][1]]
            cutted = 0
            for rng in ranges:
                message.text = message.text[: rng[0] - cutted] + message.text[1 + rng[1] - cutted:]
                cutted += 1 + rng[1] - rng[0]
            message.text = message.text.strip()
            if message.text != "":
                emote = None
                emote_count = 0
        else:
            emote = None
            emote_count = 0

        # Load previous state
        state_user = await self._state_manager.get_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER)
        state_emote = await self._state_manager.get_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE)
        state_height = await self._state_manager.get_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT)
        state_dir = await self._state_manager.get_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION)
        state_exists = bool(state_user) or bool(state_emote) or bool(state_height) or bool(state_dir)

        if state_exists and (not emote or emote != state_emote):
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION)
            if state_height >= 3 or state_dir == "DOWN":
                await self.send_response(chat=channel, message=f"@{user} поломал пирамидку @{state_user}. Ехехе")
                return HandlerResult.HANDLED
            else:
                return HandlerResult.HANDLED_AND_CONTINUE

        if not state_exists and emote and emote_count == 1:
            # Начало пирамидки
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER, value=user)
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE, value=emote)
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT, value=emote_count)
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION, value="UP")
            return HandlerResult.HANDLED_AND_CONTINUE
        if emote == state_emote and (emote_count == state_height + 1) and state_dir == "UP":
            # +1 вверх
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER, value=user)
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE, value=emote)
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT, value=emote_count)
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION, value="UP")
            return HandlerResult.HANDLED_AND_CONTINUE
        elif emote == state_emote and (emote_count == state_height - 1) and state_dir == "UP":
            # развернулись вниз
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER, value=user)
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE, value=emote)
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT, value=emote_count)
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION, value="DOWN")
            return HandlerResult.HANDLED_AND_CONTINUE
        elif emote == state_emote and (emote_count == state_height - 1) and emote_count > 1 and state_dir == "DOWN":
            # -1
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER, value=user)
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE, value=emote)
            await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT, value=emote_count)
            # await self._state_manager.set_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION, value="DOWN")
            return HandlerResult.HANDLED_AND_CONTINUE
        elif emote == state_emote and (emote_count == 1) and state_dir == "DOWN":
            # закончили пирамидку
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.USER)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.EMOTE)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.HEIGHT)
            await self._state_manager.del_state(channel=channel, command=self.COMMAND_NAME, param=SMParam.DIRECTION)
            await self.send_response(chat=channel, message=f"@{user} достроил пирамидку! Молодец!")
            return HandlerResult.HANDLED

        return HandlerResult.SKIPED



class MessagesHandlerManager:
    def __init__(self, storage: StateManager, send_message: Callable[..., Awaitable[None]]):
        self.handlers: list[CommonMessagesHandler] = []
        self._sm = storage
        self._send_message = send_message

    def register(self, command: type[CommonMessagesHandler]):
        self.handlers.append(command(self._sm, self._send_message))

    async def handle(self, user_settings: TwitchUserSettings, channel: str, message: ChatMessage):
        logger.debug(f"Handling message with {self}")
        for handler in self.handlers:
            if not handler.is_enabled(user_settings):
                continue
            res: HandlerResult = await handler.handle(channel, message)
            if res == HandlerResult.HANDLED:
                return


class UnlurkHandler(CommonMessagesHandler):
    COMMAND_NAME = "lurk"
    UNLURK_AFTER = 300

    def __init__(self, sm: StateManager, send_message: Callable[..., Awaitable[None]]):
        from dependencies import get_chat_bot
        self.chat_bot = next(get_chat_bot())
        super().__init__(sm, send_message)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True or streamer_settings.enable_lurk

    async def handle(self, channel: str, message: ChatMessage) -> HandlerResult:
        if any(x in message.text for x in ("!lurk", "!unlurk", "!лурк", "!анлурк")):
            return HandlerResult.SKIPED

        user = message.user.display_name.lower()

        previous_state: float = await self._state_manager.get_state(channel=channel, user=user, command=self.COMMAND_NAME)
        if previous_state is not None and time() - previous_state > self.UNLURK_AFTER:
            await self._state_manager.set_state(channel=channel, user=user, command=self.COMMAND_NAME, value=None)
            last_active = await self.chat_bot.get_user_last_active(channel, user)
            if time() - last_active > self.UNLURK_AFTER:
                await self.send_response(chat=channel, message=f"@{user}, с возвращением из лурка!")
            return HandlerResult.HANDLED_AND_CONTINUE
        return HandlerResult.SKIPED


class HelloHandler(CommonMessagesHandler):
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def handle(self, channel: str, message: ChatMessage) -> HandlerResult:
        if message.reply_parent_user_login == "quantum075bot":
            message.text = "@quantum075bot " + message.text
        if '@quantum075bot' in message.text.lower() and any(hello_word in message.text.lower() for hello_word in {"привет", "дарова", "здравствуй", "кваствуй", "здорова"}):
            replies = [
                f"@{message.user.display_name}, и тебе привет!",
                f"@{message.user.display_name}, здравствуй-здравствуй!",
                f"@{message.user.display_name}, дарова! >w<",
            ]
            if channel.lower() in ('anna_toad', 'toad_anna'):
                replies = [
                    f"@{message.user.display_name}, кваствуй! >w<",
                    f"Кваствуй, @{message.user.display_name}! <3",
                ]
            if channel.lower() == 'glumarkoj':
                replies = [
                    f"@{message.user.display_name}, здорова, брат!",
                ]
            await self.send_response(chat=channel, message=random.choice(replies))
            return HandlerResult.HANDLED
        return HandlerResult.SKIPED


class IAmBotHandler(CommonMessagesHandler):
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def handle(self, channel: str, message: ChatMessage) -> HandlerResult:
        if re.match(r"@quantum075bot .{0,5}бот\?", message.text.lower()):
            if random.random() < 0.1:
                await asyncio.sleep(0.5)
                await self.send_response(chat=channel, message=f"Конеяно я бот!")
                await asyncio.sleep(2)
                await self.send_response(chat=channel, message=f"конечно* 👀")
            else:
                replies = [
                    f"@{message.user.display_name}, конечно я бот! Какие могут быть сомнения?",
                    f"@{message.user.display_name}, да, я бот, и я горжусь этим!",
                    f"@{message.user.display_name}, почему ты так думаешь?",
                    f"@{message.user.display_name}, нет, я настоящий живой человек, @Quantum075 держит меня в подвале и заставляет отвечать на сообщения Т_Т",
                    f"@{message.user.display_name}, MrDestructoid !",
                ]
                await self.send_response(chat=channel, message=random.choice(replies))
            return HandlerResult.HANDLED

        if re.match(r"(кто )?боты? (- )?(плюс|плюсик|плюсики|плюсаните|\+)( в ча[тч])", message.text.lower()):
            await self.send_response(chat=channel, message="+")
            return HandlerResult.HANDLED

        return HandlerResult.SKIPED


# TODO: "спасибо", "пожалуйста", "молодец", "умничка", "пасиба", "спс", "благодарю"
