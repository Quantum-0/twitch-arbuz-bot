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
                "воняеш 🌚",
            ]
        )

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice(
            [
                f"Мы уже дегустировали тебя, @{user}. Давай попозже!",
                "Мы же несколько секунд назад твою вкусность, что за нетерпеливость!",
            ]
        )

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        return random.choice([
            f"Проверяю @{user} на вкус. Результат: {new_value}",
            f"Оцениваю вкусность @{user}. Результат: {new_value}",
            f"Облизываю @{user} для анализа. Результат: {new_value}",
        ])

    async def _target_selected(self, user: str, targets: list[str]):
        # variants = [
        #     f"{user}, а ты зачем чужими бананами интересуешься?",
        #     f"{user}, ай-яй-яй, неприлично чужие бананы трогать!",
        #     f"{user}, не трожь чужие бананы!",
        #
        #     f"{user}, ну-ка руки убери! Чужие бананы — это святое!",
        #     f"{user}, за такое можно и бан получить… бананом по лбу.",
        #     f"{user}, хочешь посмотреть чужой банан? А разрешение кто спрашивать будет?",
        #     f"{user}, ну вот зачем тебе чужой банан? Своего не хватает?",
        #     f"{user}, я конечно понимаю любопытство, но банан — это личное.",
        # ]
        # return random.choice(variants)
        return None

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

