import random
from typing import Any

from database.models import User, TwitchUserSettings
from twitch.chat.base.saving_result_command import SavingResultCommand


class TreatCommand(SavingResultCommand):
    command_name = "treat"
    command_aliases = ["treat", "вкусняшка", "вкусность"]
    command_description = "Оценить вашу вкусность"

    cooldown_timer = 60

    refresh_result_timer = 2 * 60

    async def result_generator(self, old_value: str | None, **kwargs: Any) -> str:
        return random.choice(
            [
                f"вкусненький на {random.randint(0, 100)}%",
                f"вкусняшка на {random.randint(0, 100)}%",
                f"аппетитненький на {random.randint(0, 100)}%",
                f"деликатес на {random.randint(0, 100)}%",
                # f"на вкус как пельмешек на {random.randint(0, 100)}%",
                # f"как домашняя шаурма на {random.randint(0, 100)}%",
                # f"как бабушкины пирожки на {random.randint(0, 100)}%",
                # f"под пивасик зайдёт на {random.randint(0, 100)}%",
                # f"шеф-повар одобряет на {random.randint(0, 100)}%",
                f"сомнительно, но вкусно на {random.randint(0, 100)}%",
                f"на любителя… на {random.randint(0, 100)}%",
                f"гастрономическое открытие на {random.randint(0, 100)}%",
                "воняеш 🌚",
                "на вкус как носок после рейда 💀",
                "опасно вкусный, требуется лицензия 🚨",
                "слишком вкусный, уберите от чата",
            ]
        )

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice(
            [
                f"Мы уже дегустировали тебя, @{user}. Давай попозже!",
                "Мы же несколько секунд назад твою вкусность, что за нетерпеливость!",
                f"@{user}, так часто нельзя — вкус притупляется!",
                "Подожди-подожди, дегустация — не фастфуд 🍔",
                f"@{user}, терпение — тоже приправа.",
                "Мы же только что тебя пробовали, ты куда так гонишь?",
                # f"@{user}, ещё секунда — и я вызову санитаров 👀", a..?
            ]
        )

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        return random.choice([
            f"Проверяю @{user} на вкус. Результат: {new_value}",
            f"Оцениваю вкусность @{user}. Результат: {new_value}",
            f"Облизываю @{user} для анализа. Результат: {new_value}",
        ])

    async def _target_selected(self, user: str, targets: list[str]):
        variants = [
            f"@{user}, эй! Мы тут своё пробуем, не чужое 😤",
            f"@{user}, смотри глазами, не языком!",
            f"@{user}, дегустатор нашёлся… без очереди!",
            f"@{user}, сначала себя попробуй 😏",
        ]
        return random.choice(variants)

    async def _handle_old(
        self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str
    ):
        variants = [
            f"Тебе так нравится, когда тебя облизывают? Ладно, давай ещё раз. Результат: {old_value}",
            f"Тебе так нравится, когда тебя дегустируют? Ладно, давай попробую снова. Результат: {old_value}",
            f"@{user} скажи честно, тебе просто нравится, что я тебя облизываю?",
        ]
        return random.choice(variants)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_treat

