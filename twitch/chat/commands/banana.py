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
        # 0.1% chance for legendary
        if random.randint(1, 1000) == 1:
            return random.choice([
                # "legendary:легендарный",
                # "legendary:золотой",
                # "legendary:платиновый",
                # "legendary:радиоактивный",
                "Золотой",
                "Платиновый",
                "Радиоактивный",
            ])
        # 2% chance for nsfw
        if random.randint(1, 50) == 1:
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

                "чересчур зрелый",
                "вяленький",
                "идеальной спелости",
                "подмёрзший",
                "переспевший",
                "токсично-жёлтый",
                "немного кривоватый",
                "подозрительно блестящий",
                "стильный",
                "модный",
                "в галстучке",
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
            return random.choice([
                f"@{user}, спрячь свой банан обратно, это же неприлично! Трясет тут своим бананом при всех, ну шо такое, ни стыда ни совести!"
                f"@{user}, спрячь свой банан обратно! {'Тут люди едят' if random.random() < 0.5 else 'У нас тут приличный стрим'} вообще-то!",
                f"@{user}, ну и банан у тебя… Не-не-не, давай-ка убирай это! Ты смущаешь {'чаттерсов' if random.random() else 'стримера'} >///<",
                # f"@{user}, хватит смущать чат своим бананом! Он *слишком*… эээ… выразительный.",
            ])
        if new_value == "зелёный":
            return random.choice([
                *[f"@{user}, твой банан ещё совсем зелёный"]*3,
                f"@{user}, твой банан ещё совсем зелёный, даже хрустит.",
                # f"@{user}, банану явно рановато… подожди ещё чуть-чуть.",
                f"@{user}, зелёный банан — это стиль. Но есть такое можно только смелым.",
            ])
        if new_value in {"Золотой", "Платиновый", "Радиоактивный"}:
            return f"Вау, поздравляю, @{user}, ты выбиваешь легендарку! Твой банан - {new_value}!"

        return random.choice([
            *[f"@{user}, твой банан — {new_value}"]*3,
            #f"@{user}, изучив банан под микроскопом… Вердикт: он {new_value}",
            f"@{user}, банановая комиссия постановила: банан {new_value}",
            f"@{user}, честно говоря, я бы таким бананом гордился. Ведь он - {new_value}",
        ])

    async def _target_selected(self, user: str, targets: list[str]):
        variants = [
            f"{user}, а ты зачем чужими бананами интересуешься?",
            f"{user}, ай-яй-яй, неприлично чужие бананы трогать!",
            f"{user}, не трожь чужие бананы!",

            f"{user}, ну-ка руки убери! Чужие бананы — это святое!",
            f"{user}, за такое можно и бан получить… бананом по лбу.",
            f"{user}, хочешь посмотреть чужой банан? А разрешение кто спрашивать будет?",
            f"{user}, ну вот зачем тебе чужой банан? Своего не хватает?",
            f"{user}, я конечно понимаю любопытство, но банан — это личное.",
        ]
        return random.choice(variants)

    async def _handle_old(
        self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str
    ):
        if old_value == "nsfw":
            return random.choice([
                f"Говоришь спрячь свой банан, это неприлично - нет, блин, не слушает, снова достаёт и хвастается перед всеми своим бананом! >_<"
                f"Я ж просил спрятать банан! @{user}, ну сколько можно им размахивать?",
                f"@{user}, опять этот банан всем показываешь… Эх, нет тебе покоя.",
                # f"@{user}, твой банан снова в режиме NSFW… Ты это специально, да?",
            ])
        variants = [
            f"Ну мы же только что смотрели.. Лаадно, давай ещё раз. Твой банан - {old_value}, @{user}",
            f"@{user}, твой банан всё ещё {old_value}",
            f"@{user}, почему тебя так часто беспокоит твой банан? Он всё ещё {old_value}, не беспокойся",
            f"@{user}, он всё ещё {old_value}",
            f"@{user}, думаешь что-то успело так быстро поменяться? Нет, твой банан всё так же {old_value}",

            f"@{user}, банан за 5 минут не поменяется… хотя… нет, всё ещё {old_value}.",
            f"Я опять смотрю на банан @{user}… И он всё тот же: {old_value}.",
            f"@{user}, если смотреть на банан долго, он не меняется. Проверено — {old_value}.",
            f"Банан @{user} проверен повторно: {old_value}. Стабильно!",
            f"@{user}, у твоего банана стабильная жизнь — он всё ещё {old_value}.",
        ]
        return random.choice(variants)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_banana

