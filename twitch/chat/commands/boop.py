import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.utils import delay_to_seconds, join_targets


class BoopCommand(SimpleTargetCommand):
    command_name = "boop"
    command_aliases = ["boop", "буп", "бупнуть"]
    command_description = "Бупнуть пользователя чата в нос :з"

    need_target = True
    cooldown_timer = 60
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_boop

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        target = join_targets(targets)
        if len(targets) == 1 and random.random() < 0.1:
            return f"@{user} делает буп в нось {target}, но {target} внезапно чихает от этого. @{user}, кажется тебе стоит пойти помыть руку.."
        return f"@{user} делает буп в нось {target} !"

    async def _no_target_reply(self, user: str) -> str | None:
        return f'Чтобы бупнуть кого-нибудь в носярку, нужно указать, кого ты хочешь бупнуть! Например "!буп @{user}"'

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return f"@{user}, подожди {delay_to_seconds(delay)}, прежде чем делать бупать снова :з"

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"@{user} тыкает себя пальцем в нос",
                f'@{user} загадочно ощупывает свой нос о-о"',
            ]
        )

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice(
            [
                f"Прости, @{user}, но мы не можем бупнуть в нось бота. У ботов нет носов О:"
            ]
        )

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"*удивлённо скосил глаза и смотрит на свой нос*",
                f"{user} нось мой трогаешь? с: И как он тебе?",
            ]
        )

