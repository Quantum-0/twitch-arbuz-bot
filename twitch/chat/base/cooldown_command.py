import logging
from abc import abstractmethod
from time import time

from database.models import User
from routers.schemas import ChatMessageWebhookEventSchema
from twitch.chat.base.base_command import Command
from twitch.state_manager import SMParam

logger = logging.getLogger(__name__)


class SimpleCDCommand(Command):
    @property
    @abstractmethod
    def cooldown_timer_per_chat(self) -> int | None:
        return None

    @property
    @abstractmethod
    def cooldown_timer_per_user(self) -> int | None:
        return None

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema):
        user: str = message.chatter_user_name
        user_id: int = message.chatter_user_id

        last_command_call_user = await self._state_manager.get_state(
            channel=streamer.login_name,
            user=user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN,
        )
        last_command_call_channel = await self._state_manager.get_state(
            channel=streamer.login_name,
            command=self.command_name,
            param=SMParam.COOLDOWN,
        )

        if (
            last_command_call_user
            and self.cooldown_timer_per_user
            and time() - last_command_call_user < self.cooldown_timer_per_user
        ):
            logger.debug(
                f"Skip command {self.command_name} because of per-user cooldown"
            )
            delay = self.cooldown_timer_per_user - int(time() - last_command_call_user)
            response = await self._cooldown_reply(user, delay)
            await self.send_response(chat=streamer, message=response)
            return

        if (
            last_command_call_channel
            and self.cooldown_timer_per_chat
            and time() - last_command_call_channel < self.cooldown_timer_per_chat
        ):
            logger.debug(
                f"Skip command {self.command_name} because of per-channel cooldown"
            )
            delay = self.cooldown_timer_per_chat - int(
                time() - last_command_call_channel
            )
            response = await self._cooldown_reply(user, delay)
            await self.send_response(chat=streamer, message=response)
            return

        if self.cooldown_timer_per_chat:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                command=self.command_name,
                param=SMParam.COOLDOWN,
                value=time(),
            )
        if self.cooldown_timer_per_user:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                user=user_id,
                command=self.command_name,
                param=SMParam.COOLDOWN,
                value=time(),
            )

        logger.debug(f"Handling with command handler")
        response = await self._handle(streamer, user, message.message.text)
        await self.send_response(chat=streamer, message=response)
        return

    @abstractmethod
    async def _handle(self, streamer: User, user: str, message: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        raise NotImplementedError
