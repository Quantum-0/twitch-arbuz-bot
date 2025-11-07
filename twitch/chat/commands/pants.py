import random
from collections.abc import Awaitable, Callable
from functools import partial
from time import time

from database.models import TwitchUserSettings, User
from twitch.chat.base.cooldown_command import SimpleCDCommand
from twitch.state_manager import SMParam, StateManager
from twitch.utils import extract_targets


class PantsCommand(SimpleCDCommand):
    command_name = "трусы"
    command_aliases = ["трусы", "pants"]
    command_description = "Запустить розыгрыш трусов"

    cooldown_timer_per_chat = 120
    cooldown_timer_per_user = 300
    cooldown_timer_per_target = 600

    def __init__(
        self, sm: StateManager, send_message: Callable[..., Awaitable[None]]
    ) -> None:
        super().__init__(sm, send_message)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pants

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        # Проверка — идёт ли уже розыгрыш
        pants_user = await self._state_manager.get_state(
            channel=streamer.login_name, command=self.command_name, param=SMParam.USER
        )
        if pants_user:
            return f"Невозможно начать новый розыгрыш трусов, пока не разыграли трусы @{pants_user}"

        # Выбор цели
        target: str | None = None
        if message.startswith("!трусы @"):
            targets = await extract_targets(
                message, streamer.login_name, partial(self.chat_bot.get_random_active_user, streamer)
            )  # TODO replace with display name
            if len(targets) > 1:
                return "Для розыгрыша трусов нужно выбрать только одну цель!"
            target = targets[0][1:]

        if not target:
            targets = [
                x
                for x, y in await self.chat_bot.get_last_active_users(
                    streamer.login_name
                )
            ]
            target = random.choice(targets)

        # Проверяем кулдаун для цели
        last_ts = await self._state_manager.get_state(
            channel=streamer.login_name,
            command=self.command_name,
            user=target,
            param=SMParam.TARGET_COOLDOWN,
        )
        if last_ts and time() - last_ts < self.cooldown_timer_per_target:
            return f"Трусы @{target} уже недавно разыгрывались. Подождём немного!"

        return "WiP"
        # # Запускаем розыгрыш
        # await self._state_manager.set_state(channel, self.command_name, SMParam.USER, target)
        # await self._state_manager.set_state(channel, self.command_name, SMParam.PARTICIPANTS, set())
        # await self.send_response(chat=channel, message=f"Внимание, розыгрыш трусов @{target}! Ставьте '+' в чат!")
        #
        # # Запускаем асинхронный таймер
        # asyncio.create_task(self.finish_raffle(channel, target))

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""
