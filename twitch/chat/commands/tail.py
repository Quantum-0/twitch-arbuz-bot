import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.saving_result_command import SavingResultCommand


class TailCommand(SavingResultCommand):
    command_name = "tail"
    command_aliases = ["tail", "хвост", "хвостик"]
    command_description = "У вас есть хвост? Так давайте померяем его длину!"

    cooldown_timer = 45

    refresh_result_timer = 10 * 60

    async def result_generator(self, old_value: str | None) -> str:
        if old_value is None:
            return str(random.randint(0, 5000))
        if old_value[0] in ["+", "-"]:
            old_value = old_value[1:]
        if random.random() < 0.5:
            new_value = min(5000, int(old_value) + random.randint(1, 250))
            return f"+{new_value}"
        else:
            new_value = max(0, int(old_value) - random.randint(1, 250))
            return f"-{new_value}"

    def convert_tail(self, value: int) -> str:
        if value < 10:
            return f"{value} мм"
        elif value < 100:
            return f"{value / 10} см"
        else:  # if value < 1000:
            return f"{value // 100 / 10} м"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice(
            [f"Боюсь, пока рано измерять твой хвост. Он не растёт так быстро!"]
        )

    async def _target_selected(self, user: str, targets: list[str]):
        return None

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        change = new_value[0] if new_value[0] in ["+", "-"] else None
        value = int(new_value[1:]) if new_value[0] in ["+", "-"] else int(new_value)

        if value < 10:
            result = f"@{user} твой хвост.. стоп.. а где он? А, вот же! Коротенький, всего-лишь {self.convert_tail(int(value))}"
        elif value < 100:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Коротенький :3"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value < 200:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Маленький =w="
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value < 500:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Нормальный такой OwO"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value < 1000:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Хороооший, большооой!"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value < 2500:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Нифига себе хвостище!"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value <= 5000:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Вот это гигант *О*"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        return result

    async def _handle_old(
        self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str
    ):
        variants = [
            f"Ну мы же только что смотрели.. Лаадно, давай ещё раз. Длина твоего хвоста - {self.convert_tail(int(old_value))}, @{user}",
            f"@{user}, твой хвост всё ещё {self.convert_tail(int(old_value))}",
            f"@{user}, думаешь что-то успело так быстро поменяться? Нет, твой хвост всё так же {self.convert_tail(int(old_value))}",
        ]
        return random.choice(variants)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_tail