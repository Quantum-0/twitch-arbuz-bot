import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.utils import join_targets


class BonkCommand(SimpleTargetCommand):
    command_name = "bonk"
    command_aliases = ["bonk", "бонк", "боньк", "стукнуть", "ударить"]
    command_description = "Кто-то плохо себя ведёт в чатике? Бонькни его! >w<"

    need_target = True
    cooldown_timer = 60
    cooldown_count = 1

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_bonk

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        target = join_targets(targets)
        my_answer: str = (
            f"Никаких боньков {random.choice(['на этом стриме', 'в этом чатике', 'в мою смену'])}!"
            f" @{user} {random.choice(['мягко', 'нежно', 'аккуратно', 'мягенько', 'нежненько', 'аккуратненько'])}"
            f" поглаживает {target} по {'головушке' if len(targets) == 1 else 'головушкам'} <3"
        )
        if streamer.login_name == "quantum075":
            return my_answer

        return random.choice([
            f"@{user} стучит по {target} надувным молотком. *БОНЬК*!",
            f"@{user} с размаху шлепает {target} свернутой газетой. {'Плохой' if len(targets) == 1 else 'Плохие'} {target}!",
            f"@{user} дает дружеский подзатыльник {target}, чтобы лучше думалось.",
            f"@{user} легонько тюкает {target} по макушке, просто чтобы привлечь внимание.",
            f"@{user} проверяет прочность {target}, постукивая по {'лбу' if len(targets) == 1 else 'их лбам'} пальцем.",
            f"@{user} подходит и комично стучит по {target}, как в старых мультиках.",
            my_answer,
        ])

    async def _no_target_reply(self, user: str) -> str | None:
        return f'Чтобы кого-нибудь бонькнуть, нужно указать, кого именно :з Например: "!боньк @{user}"'

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"@{user} зачем-то избивает себя свёрнутой в трубочку газетой О.О",
                f"@{user} проверяет прочность своего лба о.О",
                f"@{user} а себя то за что? O-O\" Может, лучше кого-нибудь другого? :>",
            ]
        )

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([
            f"Ботам и так тяжело живётся, а ты, @{user}, их ещё и бонькаешь?(",
            f"@{user} ты за что так с моим другом?! А если мы тебя бонькнем нашими металлическими лапками в ответ?! >:C",
        ])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"Ауч! За шоооо O^O",
                f"@{user}, а если я тебя щас стукну?! >:C",
            ]
        )
