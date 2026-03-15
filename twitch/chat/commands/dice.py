import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.saving_result_command import SavingResultCommand
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.state_manager import SMParam


class DiceCommand(SimpleTargetCommand):
    command_name = "dice"
    command_aliases = ["dice", "d6", "d8", "d12", "d20", "d100", "поднять", "монетка"]
    command_description = "Кинуть кубик"

    need_target = False
    cooldown_timer = 3
    cooldown_count = 1

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_dice

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        max_value = 0
        to_grab = False
        if "dice" in message:
            return "Нужно указать, какой именно кубик кидаешь :з Варианты: !d2 !d6 !d8 !d12 !d20 и !d100"
        if "d6" in message:
            max_value = 6
        elif "d8" in message:
            max_value = 8
        elif "d12" in message:
            max_value = 12
        elif "d20" in message:
            max_value = 20
        elif "d100" in message:
            max_value = 100
        elif "d2" in message or "монетка" in message:
            max_value = 2
        elif "!поднять" in message:
            to_grab = True

        is_fallen = bool(await self._state_manager.get_state(
            channel=streamer.login_name,
            user=user.lower(),
            command=self.command_name,
            param=SMParam.PREVIOUS_VALUE,
        ))

        if max_value == 2:
            return f"@{user} кидает монетку и выпадает {'орёл' if random.random() < 0.5 else 'решка'}"
        if max_value and not is_fallen and not to_grab:
            if random.random() < 0.05:
                await self._state_manager.set_state(
                    channel=streamer.login_name,
                    user=user.lower(),
                    command=self.command_name,
                    param=SMParam.PREVIOUS_VALUE,
                    value=True,
                )
                return f"@{user} кидает кубик, но тот падает на пол! Oh noo 😱 Теперь нужно поднять кубик, используя команду !поднять"
            random_value = random.randint(1, max_value)
            return f"@{user} кидает кубик и на нём выпадаёт число {random_value}"
        elif max_value and is_fallen and not to_grab:
            return f"@{user}, ты не можешь кинуть кубик, пока не поднимешь его командой !поднять"
        elif not max_value and not is_fallen and to_grab:
            return f"@{user} не нужно поднимать кубик. Он не упал"
        elif not max_value and is_fallen and to_grab:
            await self._state_manager.del_state(
                channel=streamer.login_name,
                user=user.lower(),
                command=self.command_name,
                param=SMParam.PREVIOUS_VALUE,
            )
            return f"@{user} поднимает кубик с пола и теперь может его кинуть!"
        return ""

    async def _no_target_reply(self, user: str) -> str | None:
        return await self._handle(None, user, "", [])

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    async def _self_call_reply(self, user: str) -> str | None:
        return await self._handle(None, user, "", [])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return None

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return None
