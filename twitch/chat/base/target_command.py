import logging
from abc import ABC, abstractmethod
from functools import partial
from time import time

from database.models import User
from routers.schemas import ChatMessageWebhookEventSchema
from twitch.chat.base.base_command import Command
from twitch.state_manager import SMParam
from twitch.utils import extract_targets

logger = logging.getLogger(__name__)


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

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema):
        targets = (
            [f"@{message.reply.parent_user_name}"]
            if message.reply and message.reply.parent_user_name
            else []
        )
        user: str = message.chatter_user_name
        user_id: int = int(message.chatter_user_id)

        if self.need_target:
            targets.extend(
                await extract_targets(
                    message.message.text,
                    message.broadcaster_user_name,
                    partial(self.chat_bot.get_random_active_user, streamer),
                )
            )
            if len(targets) == 0 and self.need_target:
                response = await self._no_target_reply(user)
                await self.send_response(chat=streamer, message=response)
                return

        last_command_call = await self._state_manager.get_state(
            channel=streamer.login_name,
            user=user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN,
        )
        command_calls_count = await self._state_manager.get_state(
            channel=streamer.login_name,
            user=user_id,
            command=self.command_name,
            param=SMParam.CALL_COUNT,
        )

        if (
            last_command_call
            and self.cooldown_timer
            and time() - last_command_call < self.cooldown_timer
        ):
            if (
                self.cooldown_count == 1
                or (command_calls_count or 1) >= self.cooldown_count
            ):
                delay = self.cooldown_timer - int(time() - last_command_call)
                response = await self._cooldown_reply(user, delay)
                await self.send_response(chat=streamer, message=response)
                return
            if self.cooldown_count > 1:
                await self._state_manager.set_state(
                    channel=streamer.login_name,
                    user=user_id,
                    command=self.command_name,
                    param=SMParam.CALL_COUNT,
                    value=command_calls_count + 1,
                )
        else:
            if self.cooldown_timer:
                await self._state_manager.set_state(
                    channel=streamer.login_name,
                    user=user_id,
                    command=self.command_name,
                    param=SMParam.COOLDOWN,
                    value=time(),
                )
                if self.cooldown_count > 1:
                    await self._state_manager.set_state(
                        channel=streamer.login_name,
                        user=user_id,
                        command=self.command_name,
                        param=SMParam.CALL_COUNT,
                        value=1,
                    )

        if len(targets) == 1 and user.lower() == targets[0][1:].lower():
            response = await self._self_call_reply(user)
            if response:
                await self.send_response(chat=streamer, message=response)
                return
        if len(targets) == 1 and targets[0].lower() in {
            "@streamelements",
            "@wisebot",
            "@wizebot",
            "@WSBot",
            "@alurarin",
            "@nightbot",
            "@botrixoficial",
            "@dustyfox_bot",
            "@moobot",
            "@jeetbot",
            "@fossabot",
        }:
            response = await self._bot_call_reply(user, target=targets[0])
            if response:
                await self.send_response(chat=streamer, message=response)
                return
        if len(targets) == 1 and targets[0].lower() == "@quantum075bot":
            response = await self._this_bot_call_reply(user)
            if response:
                await self.send_response(chat=streamer, message=response)
                return

        response = await self._handle(streamer, user, message.message.text, targets)
        await self.send_response(chat=streamer, message=response)

        for target in targets:
            if target.startswith("@"):
                target = target[1:].lower()
            else:
                continue
            # TODO: сюда б айди писать а не строку, но не оч понятно где б его взять
            await self._state_manager.set_state(
                channel=streamer.login_name,
                user=target,
                command=self.command_name,
                param=SMParam.LAST_APPLY,
                value=time(),
            )

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
    async def _handle(
        self,
        streamer: User,
        user: str,
        message: str,
        targets: list[str],
    ) -> str:
        raise NotImplementedError
