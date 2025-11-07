import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.utils import delay_to_seconds, join_targets


class BiteCommand(SimpleTargetCommand):
    command_name = "bite"
    command_aliases = ["bite", "кусь", "кусьб", "укусить", "куснуть"]
    command_description = "Укусить пользователя чата"

    need_target = True
    cooldown_timer = 45
    cooldown_count = 2

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_bite

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        kind_of_bite = [
            "злобный",
            "приятный",
            "мягкий",
            "нежный",
            "аккуратный",
            "агрессивный",
            "коварный",
        ]
        target_to_bite = [
            "левое ухо",
            "правое ухо",
            "пятку",
            "хвост",
            "ногу",
            "пэрсики",
            "нос",
            "плечо",
            "жёпку",
            "палец",
            "животик",
        ]
        target = join_targets(targets)
        # TODO: кусает, делает кусь, кусявкает, покусывает?
        return f"@{user} делает {random.choice(kind_of_bite)} кусь {target} за {random.choice(target_to_bite)}"

    async def _no_target_reply(self, user: str) -> str | None:
        return f'Чтобы укусить кого-то, нужно указать, кого именно кусаешь. Например "!кусь @{user}"'

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        if random.random() < 0.5:
            return f"@{user}, твои зубки находятся в перезарядке! Подожди чуть-чуть, прежде чем пользоваться командой снова."
        return f"@{user}, твои зубки устали кусаться, подожди {delay_to_seconds(delay)}, прежде чем делать новый кусь!"

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice(
            [f"@{user} кусает сам себя о.О", f"@{user} совершает САМОКУСЬ!"]
        )

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice(
            [
                f"{target} простите за беспокойство, коллега-бот, но пользователь @{user} делает вам кусьб"
            ]
        )

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"@{user}, а меня то за что?!",
                f"Меня кусать нельзя, кусай кого-нибудь другого!",
                f"Ну капец, уже на ботов своими зубами нападают..",
                f"@{user}, щас как сам тебя укушу >:c Банхамером!!!",
            ]
        )
