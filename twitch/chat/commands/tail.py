import random
from typing import Any

from database.models import TwitchUserSettings, User
from twitch.chat.base.saving_result_command import SavingResultCommand

_TAIL_SIZE_COMMENTS: tuple[tuple[int, str], ...] = (
    (100, " Коротенький :3"),
    (200, " Маленький =w="),
    (500, " Нормальный такой OwO"),
    (1000, " Хороооший, большооой!"),
    (2500, " Нифига себе хвостище!"),
)
_TAIL_GIANT_COMMENT = " Вот это гигант *О*"
_TAIL_CHANGE_SUFFIXES = {"+": " Подрос :з", "-": " Укоротился >.<"}


class TailCommand(SavingResultCommand):
    command_name = "tail"
    command_aliases = ["tail", "хвост", "хвостик"]
    command_description = "У вас есть хвост? Так давайте померяем его длину!"

    cooldown_timer = 45

    refresh_result_timer = 10 * 60

    async def result_generator(self, old_value: str | None, **kwargs: Any) -> str:
        # WFA: y = x * 775 + x*x * 65 ** 2 chart x from 0 to 1
        def formula(x: float) -> int:
            return int(x * 775 + (x * 65) ** 2)

        if old_value is None:
            return str(formula(random.random()))

        _, old = self._parse_delta_value(old_value)
        new_value = formula(random.random())

        if new_value == old:
            return f"{new_value}"
        if new_value > old:
            return f"+{new_value}"
        return f"-{new_value}"

    def convert_tail(self, value: int) -> str:
        if value < 100 and value % 10 != 0:
            return f"{value} мм"
        if value < 750:
            if value % 10 == 0:
                return f"{int(value // 10)} см"
            return f"{value / 10} см"
        if value % 1000 == 0:
            return f"{int(value // 1000)} м"
        return f"{value // 100 / 10} м"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice(["Боюсь, пока рано измерять твой хвост. Он не растёт так быстро!"])

    async def _target_selected(self, user: str, targets: list[str]):
        return None

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        change, value = self._parse_delta_value(new_value)

        if value < 2:
            return f"@{user}, у тебя НЕТ ХВОСТА О__О"
        if value < 10:
            return (
                f"@{user} твой хвост.. стоп.. а где он? А, вот же! Коротенький, всего-лишь {self.convert_tail(value)}"
            )

        result = f"@{user}, длина твоего хвоста - {self.convert_tail(value)}."
        comment = next(
            (text for threshold, text in _TAIL_SIZE_COMMENTS if value < threshold),
            _TAIL_GIANT_COMMENT,
        )
        if random.random() < 0.5:
            return result + comment
        return result + _TAIL_CHANGE_SUFFIXES.get(change or "", "")

    async def _handle_old(self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str):
        variants = [
            f"Ну мы же только что смотрели.. Лаадно, давай ещё раз. Длина твоего хвоста - {self.convert_tail(int(old_value))}, @{user}",
            f"@{user}, твой хвост всё ещё {self.convert_tail(int(old_value))}",
            f"@{user}, думаешь что-то успело так быстро поменяться? Нет, твой хвост всё так же {self.convert_tail(int(old_value))}",
        ]
        return random.choice(variants)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_tail
