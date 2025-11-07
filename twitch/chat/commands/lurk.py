import random
from time import time

from database.models import TwitchUserSettings, User
from twitch.chat.base.cooldown_command import SimpleCDCommand


class LurkCommand(SimpleCDCommand):
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    cooldown_timer_per_user = 30
    cooldown_timer_per_chat = None

    command_name = "lurk"
    command_aliases = ["lurk", "unlurk", "лурк", "анлурк"]
    command_description = (
        "Сообщить стримеру и чатику, что вы уходите в лурк или возвращаетесь из него"
    )

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_lurk

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        state: bool = not ("unlurk" in message or "анлурк" in message)
        previous_state: bool = (
            await self._state_manager.get_state(
                channel=streamer.login_name,
                user=user.lower(),
                command=self.command_name,
            )
            is not None
        )

        if state == previous_state and state is True:
            return f"@{user}, ты и так уже в лурке"

        if state and not previous_state:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                user=user.lower(),
                command=self.command_name,
                value=time(),
            )
            variants = [
                f"@{user} прячется за холодильник и наблюдает за стримом оттуда. Спасибо за лурк!",
                f"@{user} спотыкается об камушек, падает и проваливается в лурк",
                f"У @{user} появились более важные дела, чем просмотр этого стрима, представляете?!",
                f"@{user} превращается в крокодила, погружается в ближайшую лужу, и теперь оттуда торчат только глазки 👀",
            ]
            return random.choice(variants)

        if previous_state and not state:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                user=user.lower(),
                command=self.command_name,
                value=None,
            )
            return f"@{user} выпылывает из лурка. С возвращением!"
