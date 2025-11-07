import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.saving_result_command import SavingResultCommand


class BananaCommand(SavingResultCommand):
    command_name = "banana"
    command_aliases = ["banan", "banana", "банан"]
    command_description = "Проанализировать состояние вашего банана"

    cooldown_timer = 10

    refresh_result_timer = 5 * 60

    async def result_generator(self, old_value: str | None) -> str:
        # 5% chance for nsfw
        if random.randint(1, 20) == 1:
            return "nsfw"
        return random.choice(
            [
                "зелёный",
                "жёлтый",
                "мягкий",
                "длинный",
                "сгнивший",
                "заплесневел",
                "спелый",
                "сочный",
                "сладкий",
            ]
        )

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice(
            [
                f"Мы уже смотрели на твой банан, @{user}. Давай попозже!",
                "Мы же несколько секунд назад проверяли твой банан, что за нетерпеливость!",
            ]
        )

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        if new_value == "nsfw":
            return f"@{user}, спрячь свой банан обратно, это же неприлично! Трясет тут своим бананом при всех, ну шо такое, ни стыда ни совести!"
        if new_value == "зелёный":
            return f"@{user}, твой банан ещё совсем зелёный"
        return f"@{user}, твой банан - {new_value}"

    async def _target_selected(self, user: str, targets: list[str]):
        variants = [
            f"{user}, а ты зачем чужими бананами интересуешься?",
            f"{user}, ай-яй-яй, неприлично чужие бананы трогать!",
            f"{user}, не трожь чужие бананы!",
        ]
        return random.choice(variants)

    async def _handle_old(
        self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str
    ):
        if old_value == "nsfw":
            return f"Говоришь спрячь свой банан, это неприлично - нет, блин, не слушает, снова достаёт и хвастается перед всеми своим бананом! >_<"
        variants = [
            f"Ну мы же только что смотрели.. Лаадно, давай ещё раз. Твой банан - {old_value}, @{user}",
            f"@{user}, твой банан всё ещё {old_value}",
            f"@{user}, почему тебя так часто беспокоит твой банан? Он всё ещё {old_value}, не беспокойся",
            f"@{user}, он всё ещё {old_value}",
            f"@{user}, думаешь что-то успело так быстро поменяться? Нет, твой банан всё так же {old_value}",
        ]
        return random.choice(variants)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_banana

