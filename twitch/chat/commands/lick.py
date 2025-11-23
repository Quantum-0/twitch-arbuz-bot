import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.utils import join_targets, delay_to_seconds


class LickCommand(SimpleTargetCommand):
    command_name = "lick"
    command_aliases = ["lick", "лизь", "лизнуть", "облизать"]
    command_description = "Облизнуть пользователя чата"

    need_target = True
    cooldown_timer = 60
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_lick

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        target = join_targets(targets)
        if random.random() < 0.005:
            return (
                f"@{user} высовывает свой легендарный язык длиной почти в два чата "
                f"и облизывает {target} настолько тщательно, что у модераторов появляется подозрение "
                f"в использовании читов на длину языка O_O"
            )

        random_variants = [
            f"@{user} медленно и {random.choice(['нежно', 'озорно', 'игриво'])} облизывает {target}",
            # f"@{user} проводит языком по щёчке {target}",
            # f"@{user} облизывает ухо {target} и довольно мурчит",
            f"@{user} облизывает ухо {target}",
            f"@{user} вылизывает всё лицо {target} целиком",
            f"@{user} вылизывает всё лицо {target}",
            # f"@{user} тычется своим тёплым языком в нос {target}",
            f"@{user} лижет в нос {target}",
            f"@{user} пытается лизнуть {target}, но {target} успешно уворачива{'е' if len(targets) == 1 else 'ю'}тся от нападения языком!",
        ]
        return random.choice(random_variants)

    async def _no_target_reply(self, user: str) -> str | None:
        if random.random() < 0.05:
            user = "Quantum075"
        return f'Чтобы кого-то лизнуть, нужно указать, кого именно ты хочешь лизнуть. Например "!лизь @{user}"'

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        random_variants = [
            f"@{user}, твой язык на перезарядке. Прежде чем сделать следующи лизь, подожди {delay_to_seconds(delay)}",
            f"@{user}, остановись, язык ж отвалится! Повторный лизь возможен через {delay_to_seconds(delay)}",
            f"Язык @{user} устал и не хочет двигаться. Попытка лизнуть оказалась неуспешна. Повторите через {delay_to_seconds(delay)}",
            f"@{user}, твой язык устало болтается. Дай ему отдохнуть {delay_to_seconds(delay)}",
        ]
        return random.choice(random_variants)

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"@{user} облизывает сам себя о.О",
                f"@{user} совершает САМОЛИЗЬ!",
                f"@{user} развлекается с собственным языком.",

                f"@{user} пытается лизнуть сам себя… и почти получается o_O",
                f"@{user} совершает САМОЛИЗЬ! Зачем? Почему?..",
                f"@{user} экспериментирует со своим же языком. Странно, но ладно.",
                f"@{user} облизал сам себя. Зрители в замешательстве.",
            ]
        )

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice(
            [
                f"{target} простите за беспокойство, коллега-бот, но пользователь @{user} вас только что облизнул",
                f"Пользователь @{user} произвёл акт лизания. Фиксирую…",
                f"Спасибо за демонстрацию влажности языка для @{target}, @{user}. Не повторяйте.",
            ]
        )

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"@{user}, о да, давай, облизывай меня, облизывай меня полностью",
                f"@{user}, вы что себе позволяете?! Это неприлично >.<",
                f"А-а-а-а-а! Ну мокро же >.<",
            ]
        )
