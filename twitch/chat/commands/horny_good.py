import random
from typing import Any

from database.models import TwitchUserSettings, User
from twitch.chat.base.saving_result_command import SavingResultCommand
from twitch.utils import join_targets


class HornyGoodCommand(SavingResultCommand):
    command_name = "horny_good"
    command_aliases = ["horny", "хорни"]
    command_description = "Узнать, насколько сильно вам нравится смотреть этот стрим ;)"

    cooldown_timer = 10

    refresh_result_timer = 3 * 60

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_horny_good

    async def result_generator(self, old_value: str | None, **kwargs: Any) -> str:
        if old_value is None:
            return str(random.randint(0, 100))
        if old_value[0] in ["+", "-"]:
            old_value = int(old_value[1:])
        else:
            old_value = int(old_value)
        new_value = random.randint(0, 100)
        if new_value == old_value:
            return str(new_value)
        if new_value > old_value:
            return f"+{new_value}"
        return f"-{new_value}"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice(
            [
                "Солнышко, пирожочек ты наш неприличный, мы же только что смотрели на твоё хорни Потерпи чуть-чуть пожалуйста, пока значение поменяется ;)"
            ]
        )

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        change = new_value[0] if new_value[0] in ["+", "-"] else None
        value = int(new_value[1:]) if new_value[0] in ["+", "-"] else int(new_value)

        if value < 20:
            if random.random() < 0.5:
                result = f"@{user} хорни всего-лишь на {value}%. Маловато будет, что ж ты так? Расскажи нам о своих вкусах, а мы с чатиком подумаем, как можем помочь тебе :з"
            else:
                result = f"@{user} хорни всего на {value}%. 😱 На моём стриме? И столь низкий процент?! Что ты скажешь в своё оправдание?!"
                if change == "+":
                    result += random.choice(
                        [
                            " Ну хоть с прошлого раза побольше стало..",
                            " Ну хоть с прошлого раза выросло..",
                        ]
                    )
                elif change == "-":
                    result += random.choice([" В прошлый раз побооольше было конечно.."])
        elif value < 40:
            if random.random() < 0.5:
                if change == "+":
                    result = (
                        f"@{user} хорни на {value}% и более того, я вижу как стремительно растёт твой... процент! >:з"
                    )
                elif change == "-":
                    result = f"@{user} хорни на {value}%, и я вижу как твой процент падает 😱 Надеюсь, что это временно, и в ближайшем будущем ты поднимешь свой... процент! >:з"
                else:
                    result = f"@{user} хорни на {value}% и я уповаю на то, что это временно, и в ближайшем будущем ты поднимешь свой... процент! >:з"
            else:
                if change == "+":
                    result = f"@{user} хорни на {value}%. Я вижу твои сомнения, но, кажется, ты двигаешься в нужную сторону >w>"
                else:
                    result = f"@{user} хорни на {value}%. Я вижу твои сомнения, но, надеюсь, в итоге ты выберешь тёмную сторону >w>"
        elif value < 60:
            if random.random() < 0.5:
                if change == "+":
                    result = f"@{user} хорни на {value}%. Золотая середина! А тот факт, что значение подросло с прошлого раза - не может не радовать!"
                else:
                    result = f"@{user} хорни на {value}%. Золотая середина, и это прекрасно!"
            else:
                result = f"@{user} хорни на {value}%. Отличный результат, но, думаю, нам всем стоит постараться и помочь @{user} поднять это значение до максимума!"
        elif value < 80:
            if random.random() < 0.5:
                result = f"В чатике становится жарко? Давайте подкинем дров и разгорим огонь сильнее, ведь @{user} хорни на {value}%"
                if change == "+":
                    result = f"@{user} хорни на {value}%. Твой процент такой.. большой! И с каждой минутой он становится всё больше и больше! Охх~ Становится действительно жарко~"
            else:
                result = f"Спрячьте весь Anti-Horny Spray™! Мы ведь не хотим лишиться разгорающихся страстей в чате, ибо у @{user} аж целых {value}%"
        elif value < 95:
            if random.random() < 0.5:
                result = (
                    f"Гляжу, под тобой уже мокро, ведь у тебя, @{user} - {value}%! Только не затопи чат, пожалуйста ;)"
                )
                if random.random() < 0.25:
                    result += " Хотя кто знает, может они и не против ;)"
            else:
                result = f"Чат! Готовьтесь! У @{user} - {value}%, держите свои трусы, а то утащит!"
                # "Вот $randomfollowerusername уже без трусов, кто будет следующей жертвой?"
        else:  # 95-100
            if random.random() < 0.5:
                result = f"Соседи снизу стучатся в дверь и ругаются, что их затопили. А всё потому что @{user} хорни на {value}%!"
            else:
                result = f"У @{user} - {value}%, кто же станет первой жертвой? Ставьте «+» в чат, если хотите попасть под раздачу!"
        return result

    async def _target_selected(self, user: str, targets: list[str]):
        variants = [
            f"{user} недостаточно своего хорни, поэтому трогает чужое ÒwÓ",
            f"{user} посылает свои хорни-вайбы в {join_targets(targets)}",
            f"{user} хочет захорнявить {join_targets(targets)} :>",
        ]
        return random.choice(variants)

    async def _handle_old(self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str):
        value = old_value[1:] if old_value[0] in ["+", "-"] else old_value
        variants = [
            f"Мы уже узнали, что ты на {value}% хорни. Наберись терпения, мы уверены, что твой процент возрастёт :>",
            f"@{user} не терпится обновить свою хорнявность, в надежде, что она возрастёт, но нужно немного подождать :>",
        ]
        return random.choice(variants)
