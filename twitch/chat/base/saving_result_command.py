import logging
from abc import abstractmethod
from functools import partial
from time import time

from database.models import User
from routers.schemas import ChatMessageWebhookEventSchema
from twitch.chat.base.base_command import Command
from twitch.state_manager import SMParam
from twitch.utils import extract_targets

logger = logging.getLogger(__name__)


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
    async def result_generator(self, old_value: str | None) -> str:
        raise NotImplementedError

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema):
        user: str = message.chatter_user_name
        user_id: int = message.chatter_user_id

        last_command_call = await self._state_manager.get_state(
            channel=streamer.login_name,
            user=user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN,
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

        targets = await extract_targets(
            message.message.text,
            message.broadcaster_user_name,
            partial(self.chat_bot.get_random_active_user, streamer)
        )
        if targets:
            response = await self._target_selected(user, targets)
            await self.send_response(chat=streamer, message=response)
            return

        if (
            last_command_call
            and self.cooldown_timer
            and time() - last_command_call < self.cooldown_timer
        ):
            delay = self.cooldown_timer - int(time() - last_command_call)
            response = await self._cooldown_reply(user, delay)
            await self.send_response(chat=streamer, message=response)
            return
        else:
            if self.cooldown_timer:
                await self._state_manager.set_state(
                    channel=streamer.login_name,
                    user=user_id,
                    command=self.command_name,
                    param=SMParam.COOLDOWN,
                    value=time(),
                )

        if (
            self.refresh_result_timer
            and last_result_time
            and time() - last_result_time < self.refresh_result_timer
        ):
            response = await self._handle_old(
                streamer,
                user,
                message.message.text,
                last_result_value,
                time() - last_result_time,
            )
        else:
            new_value = await self.result_generator(last_result_value)
            await self._state_manager.set_state(
                user=user_id,
                command=self.command_name,
                param=SMParam.PREVIOUS_VALUE,
                value=new_value,
            )
            await self._state_manager.set_state(
                user=user_id,
                command=self.command_name,
                param=SMParam.PREVIOUS_VALUE_TIME,
                value=time(),
            )
            response = await self._handle_new(
                streamer, user, message.message.text, new_value
            )
        await self.send_response(chat=streamer, message=response)
        return

    @abstractmethod
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        raise NotImplementedError

    @abstractmethod
    async def _handle_old(
        self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str
    ):
        raise NotImplementedError

    @abstractmethod
    async def _target_selected(self, user: str, targets: list[str]):
        raise NotImplementedError
