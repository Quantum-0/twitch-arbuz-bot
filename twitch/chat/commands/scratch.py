import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.utils import delay_to_seconds, join_targets


class ScratchCommand(SimpleTargetCommand):
    command_name = "scratch"
    command_aliases = [
        "scratch",
        "чесать",
        "почесать",
        "чёсь",
    ]
    command_description = "Почесать кого-нибудь :з"

    need_target = True
    cooldown_timer = 45
    cooldown_count = 2

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_scratch

    async def _handle(self, streamer: User, user: str, message: str, targets: list[str]) -> str:
        target = join_targets(targets)
        how_scratch = random.choice(
            ["мягко", "мягенько", "осторожно", "легонько", "аккуратно", "приятно", "нежно", "ласково"]
        )
        if len(targets) == 1:
            variants = [
                f"@{user} {how_scratch} почёсывает {target} спинку",
                f"@{user} {how_scratch} почёсывает {target} за ушком",
                f"@{user} {how_scratch} почёсывает {target} животик",
                f'@{user} {how_scratch} почёсывает {target} плечо. На немой вопросительный взгляд отвечая: "мне показалось что у тебя плечо чешется о.о"',
                f"@{user} {how_scratch} проводит коготками по спинке {target}",
                f"@{user} {how_scratch} почёсывает {target} за обоими ушками одновременно",
                f"@{user} {how_scratch} чешет {target} под подбородком",
            ]
        else:
            variants = [
                f"@{user} {how_scratch} почёсывает спинки {target}",
                f"@{user} {how_scratch} почёсывает за ушками {target}",
                f"@{user} {how_scratch} почёсывает животики {target}",
                f"@{user} {how_scratch} проводит коготками по спинкам {target}",
            ]
        return random.choice(variants)

    async def _no_target_reply(self, user: str) -> str | None:
        if random.random() < 0.1:
            return f"@{user} точит когти об когтеточку. Стоп што.. @{user}, ты что, котик? И откуда тут когтеточка? о_О"
        return random.choice(
            [
                f'Чтобы кого-нибудь почесать, нужно указать цель! Например "!чёсь @{user}"',
                f"@{user} машет лапкой в пустоту — но пустота не отвечает на почёсывания :с",
                # f"@{user} чес-чес-чес… никого. Зато когти наточились!",
            ]
        )

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice(
            [
                f"@{user}, дай своим жертвам опомниться! Вернись через {delay_to_seconds(delay)}.",
                f"@{user}, почёсывание — не бесконечный ресурс. Перезарядка {delay_to_seconds(delay)}.",
            ]
        )

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"@{user} задумчиво почёсывает затылок",
                f"@{user} активно чешет голову. Кажется, её пора бы и помыть..",
                f"@{user} почёсывает свою попу, думая что никто этого не заметит",
                f"@{user} чешет себя за ушком и жмурится от удовольствия",
                f"@{user} скользит коготками по собственной руке — никого не хочется чесать, кроме себя",
                f"@{user} почёсывает макушку, пытаясь вспомнить, что хотел сделать",
                f"@{user} задумчиво чешет подбородок, размышляя о вечном",
                f"@{user} чешет животик и выглядит при этом как сытый котик",
            ]
        )

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice(
            [
                "Yay, почесушки для бота! ^w^",
                f"@{user} с неприятным металическим скрежетом проводит когтями по @{target}",
                f"@{user} с трудом находит более-менее мягкое место у бота, и принимается почёсывать @{target}",
                f"@{user} чешет @{target} за кулером — благо, у бота он как ушко.",
                f"@{target} тихо жужжит от удовольствия. Кажется, боту нравится!",
            ]
        )

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                "Yay, почесушки для меня! ^w^",
                "*довольное мурчание* ^w^",
                "*приподнимает ушки и машет хвостом* ещё!",
                "*мигает светодиодами от удовольствия* ^///^",
            ]
        )
