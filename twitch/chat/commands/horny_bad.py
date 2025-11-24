import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.saving_result_command import SavingResultCommand
from twitch.utils import join_targets


class HornyBadCommand(SavingResultCommand):
    command_name = "horny_good"
    command_aliases = ["horny", "хорни"]
    command_description = "Проверить уровень хорни, целомудренности, самоконтроля и насколько сильно вам нужно умерить свою хорнявность"

    cooldown_timer = 10

    refresh_result_timer = 3 * 60

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_horny_bad

    async def result_generator(self, old_value: str | None) -> str:
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
                f"@{user}, тормози! Мы только что измеряли твою хорни энергетику. Дай время себе остыть."
                f"Дай системе стабилизироваться и успокой свои желания :3",
                f"@{user}, ты чего так суетишься? Дай хоть немного остыть перед следующим измерением >:|",
            ]
        )

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        change = new_value[0] if new_value[0] in ["+", "-"] else None
        value = int(new_value[1:]) if new_value[0] in ["+", "-"] else int(new_value)

        # --- 0–20% — почти целомудрие ---
        if value < 10:
            result = random.choice([
                f"{value}% небесного спокойствия вокруг @{user}. ",
                # f"Чат, прячьте свои похотливые мысли — рядом святой!",
                f"@{user} обладает {value}% хорни. Настолько чист, что рядом с тобой тают даже самые хорни-зрители."
                f"{100-value}% чистоты! @{user}, ты сияешь как ледяной фонарь среди хорни-мрака чата.",
            ])
        elif value < 20:
            if random.random() < 0.5:
                result = random.choice([
                    f"@{user}, всего {value}% хорни. Прекрасно! Продолжай держать себя в руках.",
                    f"{value}% хорни. Дышим ровно, не поддаёмся импульсам, @{user}.",
                ])
            else:
                result = f"@{user}, {value}% хорни — это достойный уровень самоконтроля. Гордись собой!"
                if change == "+":
                    result += random.choice([
                        " Но будь осторожнее, что-то ты начинаешь разогреваться…",
                        " Но что это за откат..? Не разочаровывай нас.",
                    ])
                elif change == "-":
                    result += " Молодец, стало меньше — держи курс!"

        # --- 20–40% — зарождающаяся опасность ---
        elif value < 40:
            if random.random() < 0.5:
                result = random.choice([
                    f"@{user}, {value}% хорни. Опасная тропинка… Постарайся уменьшить это значение.",
                    f"Но не переоценивай себя — один неверный шаг, и всё рухнет.",
                ])
            else:
                if change == "+":
                    result = f"@{user}, {value}% хорни, и оно растёт. Так и до беды недалеко!"
                elif change == "-":
                    result = f"@{user}, {value}% хорни. Хорошо, что идёшь на спад — продолжай!"
                else:
                    result = f"@{user}, {value}% хорни. Ты на грани, будь благоразумнее."

        # --- 40–60% — красная зона ---
        elif value < 60:
            if random.random() < 0.5:
                if change == "+":
                    result = f"@{user}, аж {value}% хорни, и оно растёт! Ситуация выходит из-под контроля!"
                else:
                    result = f"@{user}, {value}% хорни. Средний уровень угрозы — мы верим, что ты справишься."
            else:
                result = random.choice([
                    f"@{user}, {value}% хорни. Нужно срочно остыть. Сделай глубокий вдох и отступи от края.",
                    f"@{user} имеет {value}% хорни — золотая середина. Но не расслабляйся: скатиться во тьму очень легко."
                ])

        # --- 60–80% — тревога! ---
        elif value < 80:
            if random.random() < 0.25:
                result = f"@{user}, {value}% хорни — это крайне тревожно! Пожалуйста, возьми себя в руки."
                if change == "+":
                    result = f"@{user}, {value}% хорни, и оно поднимается! Мы теряем тебя!"
            else:
                result = random.choice([
                    f"@{user}, {value}% хорни. Нужен срочный Anti-Horny Spray™. И много.",
                    f"@{user}, {value}% хорни — ты всё ещё опасно близок к запретной зоне.",
                    f"@{user}, {value}% хони. Ты словно идёшь по тонкому льду, не оступись.",
                ])

        # --- 80–95% — почти катастрофа ---
        elif value < 95:
            if random.random() < 0.25:
                result = f"@{user}, у тебя {value}% хорни. Это почти точка невозврата! Срочно охлади голову!"
                if random.random() < 0.25:
                    result += " Мы в тебя верим… хотя и с опаской."
            else:
                result = random.choice([
                    f"@{user}, {value}% хорни! Чат, держитесь друг за друга — сейчас может начаться буря.",
                    f"Умоляю, соберись. Хватит думать о ТАКОМ.",
                    f"@{user}, лишь {value}% НЕ-хорни. Это значит, что твой разум на грани падения. "
                    f"Возьми стакан холодной воды и ОСТАНОВИСЬ.",
                ])

        # --- 95–100% — абсолютное зло ---
        else:
            result = random.choice([
                f"@{user}, {value}% хорни — ПОЛНЫЙ КРАХ! Спрячьте всё святое и включайте вентиляцию!",
                f"@{user}, у тебя жалкие {value}% самоконтроля… Это катастрофа. ",
                f"@{user}, {value}% хорни. Все в укрытие! Это уровень угрозы «АЛЬФА»!",
            ])

        return result

    async def _target_selected(self, user: str, targets: list[str]):
        variants = [
            f"@{user} пытается наставить {join_targets(targets)} на путь приличия и целомудрия",
            f"@{user} строго смотрит на {join_targets(targets)} и требует меньше похоти.",
            f"@{user} осуждающе целится холодным взглядом в {join_targets(targets)}.",
            f"@{user} пытается перенаправить своё хорни в сторону {join_targets(targets)}, но мы этого не допустим!",
            f"@{user} смотрит на {join_targets(targets)} слишком подозрительно. Остудитесь немедленно.",
            f"@{user}, прекрати хорнить на {join_targets(targets)}. Мы следим.",
        ]
        return random.choice(variants)

    async def _handle_old(self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str):
        value = old_value[1:] if old_value[0] in ["+", "-"] else old_value
        variants = [
            f"@{user}, мы уже знаем, что ты на {value}% хорни. Подожди чуть-чуть, прежде чем снова проверять своё спокойствие.",
            f"@{user} снова хочет убедиться в своей приличности… но системе нужно время. Терпение!",
            f"@{user}, мы уже оценили твой уровень хорни: {value}%. Дай себе время успокоиться.",
            f"@{user}, прошло всего {seconds_spend} секунд. Остынь немного и загляни позже.",
        ]
        return random.choice(variants)