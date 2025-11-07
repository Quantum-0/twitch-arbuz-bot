import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand


class BushCommand(SimpleTargetCommand):
    command_name = "bush"
    command_aliases = ["куст"]
    command_description = "Опечатка в команде !кусь"

    need_target = True
    cooldown_timer = 120
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_bite

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        variants = [
            'срывает ветку с куста и кладёт себе на голову, приговаривая "я цвяточек"',
            "запрыгивает в куст и начинает издавать звуки растительности",
            "превращается в куст " + random.choice(["можжевельника", "мяты", "малины"]),
            f"берёт куст, вытаскивает его из земли и швыряет в стримера",
            "отращивает на себе пару веточек и листочков на них",
            "начинает фотосинтезировать",
        ]
        return (
            f"Опечатавшись в команде !кусь, @{user} внезапно {random.choice(variants)}"
        )

    async def _no_target_reply(self, user: str) -> str | None:
        return await self._handle(None, user, "", [])

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    async def _self_call_reply(self, user: str) -> str | None:
        return await self._handle(None, user, "", [])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return None

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return "Сам ты куст! О.О"
