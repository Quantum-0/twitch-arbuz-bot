from abc import ABC, abstractmethod
from collections.abc import Callable, Awaitable
from time import time

from twitchAPI.chat import ChatMessage

from database.models import TwitchUserSettings
from twitch.state_manager import StateManager, SMParam
from twitch.utils import extract_targets


class Command(ABC):
    @property
    @abstractmethod
    def command_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def command_description(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def command_aliases(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        raise NotImplementedError

    def __init__(self, sm: StateManager, send_message: Callable[..., Awaitable[None]]):
        self._state_manager = sm
        self.send_response = send_message
        if len(self.command_aliases) == 0:
            raise RuntimeError("Command has no trigger aliases")

    @abstractmethod
    async def handle(self, channel: str, message: ChatMessage):
        raise NotImplementedError

class SimpleTargetCommand(Command, ABC):
    @property
    @abstractmethod
    def need_target(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def cooldown_timer(self) -> int | None:
        return None

    @property
    def cooldown_count(self) -> int:
        return 1

    async def handle(self, channel: str, message: ChatMessage):
        targets = []
        user: str = message.user.display_name
        user_id: int = int(message.user.id)

        if self.need_target:
            targets = extract_targets(message.text, channel)
            if len(targets) == 0 and self.need_target:
                response = await self._no_target_reply(user)
                await self.send_response(chat=channel, message=response)
                return

        last_command_call = await self._state_manager.get_state(
            channel=channel,
            user=user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN
        )
        command_calls_count = await self._state_manager.get_state(
            channel=channel,
            user=user_id,
            command=self.command_name,
            param=SMParam.CALL_COUNT
        )

        if last_command_call and self.cooldown_timer and time() - last_command_call < self.cooldown_timer:
            if self.cooldown_count == 1 or (command_calls_count or 1) >= self.cooldown_count:
                delay = self.cooldown_timer - int(time() - last_command_call)
                response = await self._cooldown_reply(user, delay)
                await self.send_response(chat=channel, message=response)
                return
            if self.cooldown_count > 1:
                await self._state_manager.set_state(channel=channel, user=user_id, command=self.command_name, param=SMParam.CALL_COUNT, value=command_calls_count + 1)
        else:
            if self.cooldown_timer:
                await self._state_manager.set_state(channel=channel, user=user_id, command=self.command_name, param=SMParam.COOLDOWN, value=time())
                if self.cooldown_count > 1:
                    await self._state_manager.set_state(channel=channel, user=user_id, command=self.command_name, param=SMParam.CALL_COUNT, value=1)

        if len(targets) == 1 and user.lower() == targets[0][1:].lower():
            response = await self._self_call_reply(user)
            if response:
                await self.send_response(chat=channel, message=response)
                return
        if len(targets) == 1 and targets[0].lower() in {"@streamelements", "@wisebot", "@alurarin", "@nightbot", "@botrixoficial", "@dustyfox_bot", "@moobot"}:
            response = await self._bot_call_reply(user, target=targets[0])
            if response:
                await self.send_response(chat=channel, message=response)
                return
        if len(targets) == 1 and targets[0].lower() == "@quantum075bot":
            response = await self._this_bot_call_reply(user)
            if response:
                await self.send_response(chat=channel, message=response)
                return

        response = await self._handle(channel, user, message.text, targets)
        await self.send_response(chat=channel, message=response)

        for target in targets:
            if target.startswith('@'):
                target = target[1:].lower()
            else:
                continue
            # TODO: сюда б айди писать а не строку, но не оч понятно где б его взять
            await self._state_manager.set_state(channel=channel, user=target, command=self.command_name, param=SMParam.LAST_APPLY, value=time())

    @abstractmethod
    async def _no_target_reply(self, user: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _self_call_reply(self, user: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        raise NotImplementedError

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return await self._bot_call_reply(user, "@Quantum075Bot")

    @abstractmethod
    async def _handle(self, channel: str, user: str, message: str, targets: list[str]) -> str:
        raise NotImplementedError

class SimpleCDCommand(Command):
    @property
    @abstractmethod
    def cooldown_timer_per_chat(self) -> int | None:
        return None

    @property
    @abstractmethod
    def cooldown_timer_per_user(self) -> int | None:
        return None

    async def handle(self, channel: str, message: ChatMessage):
        user: str = message.user.display_name
        user_id: int = int(message.user.id)

        last_command_call_user = await self._state_manager.get_state(
            channel=channel,
            user=user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN
        )
        last_command_call_channel = await self._state_manager.get_state(
            channel=channel,
            command=self.command_name,
            param=SMParam.COOLDOWN
        )

        if last_command_call_user and self.cooldown_timer_per_user and time() - last_command_call_user < self.cooldown_timer_per_user:
            delay = self.cooldown_timer_per_user - int(time() - last_command_call_user)
            response = await self._cooldown_reply(user, delay)
            await self.send_response(chat=channel, message=response)
            return

        if last_command_call_channel and self.cooldown_timer_per_chat and time() - last_command_call_channel < self.cooldown_timer_per_chat:
            delay = self.cooldown_timer_per_chat - int(time() - last_command_call_channel)
            response = await self._cooldown_reply(user, delay)
            await self.send_response(chat=channel, message=response)
            return

        if self.cooldown_timer_per_chat:
            await self._state_manager.set_state(channel=channel, command=self.command_name, param=SMParam.COOLDOWN, value=time())
        if self.cooldown_timer_per_user:
            await self._state_manager.set_state(channel=channel, user=user_id, command=self.command_name, param=SMParam.COOLDOWN, value=time())

        response = await self._handle(channel, user, message.text)
        await self.send_response(chat=channel, message=response)
        return

    @abstractmethod
    async def _handle(self, channel: str, user: str, message: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        raise NotImplementedError

class SavingResultCommand(Command):
    @property
    @abstractmethod
    def cooldown_timer(self) -> int | None:
        return None

    @property
    @abstractmethod
    def refresh_result_timer(self) -> int | None:
        return None

    @abstractmethod
    async def result_generator(self) -> str:
        raise NotImplementedError

    async def handle(self, channel: str, message: ChatMessage):
        user: str = message.user.display_name
        user_id: int = int(message.user.id)

        last_command_call = await self._state_manager.get_state(
            channel=channel,
            user=user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN
        )
        last_result_value = await self._state_manager.get_state(
            user=user_id,
            command=self.command_name,
            param=SMParam.PREVIOUS_VALUE,
        )
        last_result_time = await self._state_manager.get_state(
            user=user_id,
            command=self.command_name,
            param=SMParam.PREVIOUS_VALUE_TIME,
        )

        targets = extract_targets(message.text, channel)
        if targets:
            response = await self._target_selected(user, targets)
            await self.send_response(chat=channel, message=response)
            return

        if last_command_call and self.cooldown_timer and time() - last_command_call < self.cooldown_timer:
            delay = self.cooldown_timer - int(time() - last_command_call)
            response = await self._cooldown_reply(user, delay)
            await self.send_response(chat=channel, message=response)
            return
        else:
            if self.cooldown_timer:
                await self._state_manager.set_state(channel=channel, user=user_id, command=self.command_name, param=SMParam.COOLDOWN, value=time())

        if self.refresh_result_timer and last_result_time and time() - last_result_time < self.refresh_result_timer:
            response = await self._handle_old(channel, user, message.text, last_result_value, time() - last_result_time)
        else:
            new_value = await self.result_generator()
            await self._state_manager.set_state(user=user_id, command=self.command_name, param=SMParam.PREVIOUS_VALUE, value=new_value)
            await self._state_manager.set_state(user=user_id, command=self.command_name, param=SMParam.PREVIOUS_VALUE_TIME, value=time())
            response = await self._handle_new(channel, user, message.text, new_value)
        await self.send_response(chat=channel, message=response)
        return

    @abstractmethod
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _handle_new(self, channel: str, user: str, text: str, new_value: str):
        raise NotImplementedError

    @abstractmethod
    async def _handle_old(self, channel: str, user: str, text: str, old_value: str, seconds_spend: str):
        raise NotImplementedError

    @abstractmethod
    async def _target_selected(self, user: str, targets: list[str]):
        raise NotImplementedError