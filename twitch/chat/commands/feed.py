import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.utils import delay_to_seconds, join_targets


class FeedCommand(SimpleTargetCommand):
    command_name = "feed"
    command_aliases = ["feed", "кормить", "покормить", "накормить", "угостить"]
    command_description = "Накормить кого-нибудь вкусняшкой >w<"

    need_target = True
    cooldown_timer = 60
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_feed

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        target = join_targets(targets)
        random_action = [
            "кормит",
            "угощает",
            "подкармливает",
        ]
        random_food = [
            "печеньками",
            "кексиком",
            "бананчиком",
            "ягодками",
            "шоколадкой",
            "мороженкой",
            "чипсеками",
            "яблочком",
        ]
        result = f"@{user} {random.choice(random_action)} {target} {random.choice(random_food)}"
        return result

    async def _no_target_reply(self, user: str) -> str | None:
        return f'Чтобы покормить кого-нибудь, нужно указать, кого ты хочешь кормить! Например "!угостить @{user}"'

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return f"@{user}, подожди {delay_to_seconds(delay)}, необходимо пополнить запасы вкусняшек"

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"@{user} жадно съедает все вкусняшки q-q",
                f"@{user} отказывается делиться и съедает всё самостоятельно >.<",
            ]
        )

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([
            f"@{user}, суёт печеньку в электронную морду бота. {target} в недоумении.",
        ])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"ОМ-НОМ-НОМ-НОМ-НОМ",
            ]
        )
