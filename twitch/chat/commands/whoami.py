import datetime
import random
from typing import Any

from database.models import TwitchUserSettings, User
from twitch.chat.base.saving_result_command import SavingResultCommand


class WhoAmICommand(SavingResultCommand):
    command_name = "whoami"
    command_aliases = ["якто", "ктоя", "whoami"]
    command_description = "Давайте познакомимся, и узнаем кто Вы!"

    cooldown_timer = 600

    refresh_result_timer = 3600

    async def result_generator(self, old_value: str | None, user_id: int, **kwargs: Any) -> str:  # noqa
        variants = [
            "Борщ, сваренный аргоновой сваркой",
            "Радостный тюленьчик",
            "Банановый омлет",
            "Валенок из попугаячьей шерсти",
            "Главный зритель в этом чатике",
            "Домашняя бензопила",
            "Древесный уголь",
            "Пылесос, напившийся воды",
            "Орецкий Грех",
            "Сломанный телевизор",
            "Постиранный пододеяльник",
            "Пакет с бытовой химией",
            "Умный голосовой помощник",
            "Маленький кирпичик",
            "Радиоактивный шпион",
            "Кресло-качалка, сбежавшее из качалки",
            "Кролик, потерявший наушник",
            "Зритель чужого стрима АГА ПОПАВСЯ ПРЕДАТЕЛЬ",
            "Бот",
            "Чистящее средство для интенсивного роста бактерий",
            "Билетик в парк аттракционов",
            "Звезда с новогодней ёлки",
            "Фикус, показывающий фокус",
            "Поезд Москва - Санкт-Петербург",
            "Йязь",
            "Кожура от сырника",
            "Лишний тапочек",
            "Лампа закаливания",
            "Рекламный буклетик",
            "Лопата, использованная не по назначению",
            "Высокочастотный радиопередатчик",
            "Крем от загара",
            "Подсолнух, отрицающий семечки",
            "Резиновая туточка, купающаяся в луже",
            "Не тот, за кого себя выдаёшь",
            "Плесень за холодильником",
            "Прыгающая настольная лампа",
            "Замороженный изюм",
            "Крайний в очереди к стоматологу",
            "Прошлогодний хлеб",
            "Прекрасный человек",
            "Симпатичный пингвинёнок в шарфике",
            "Та самая прямая палочка в тетрисе",
            "Милашка :з",
            "Сладкая пироженка",
            "Пирожок с ничем",
            "Крабовая палочка",
            "Шорты снежного человека",
            "Плесневелый мандарин",
            "Новогодний салатик",
            "Загадочный фикус",
            "Странное пятно на ковре",
        ]
        result_id = (datetime.date.today().toordinal() + user_id) % len(variants)
        return variants[result_id]

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        if delay < 120:
            return None
        return random.choice(
            [
                f"Мы уже смотрели кто ты сегодня, @{user}. В следующий раз проверишься завтра!",
                "Ты не умеешь так быстро перевоплощаться!",
            ]
        )

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        return f"Ты сегодня - {new_value}"

    async def _target_selected(self, user: str, targets: list[str]):
        return None

    async def _handle_old(
        self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str
    ):
        return f"Мы ведь уже проверяли сегодня.. Ладно, если ты так хочешь, я проверю ещё раз! Итак, ты сегодня.. {old_value}!"

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_whoami

