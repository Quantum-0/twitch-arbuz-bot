import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.utils import delay_to_seconds, join_targets


class PatCommand(SimpleTargetCommand):
    command_name = "pat"
    command_aliases = [
        "pat",
        "patpat",
        "pat-pat",
        "пат",
        "пат-пат",
        "патпат",
        "погладить",
        "гладить",
    ]
    command_description = "Пат-патнуть пользователя по голове ^w^"

    need_target = True
    cooldown_timer = 45
    cooldown_count = 2

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pat

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        target = join_targets(targets)
        how_pat = random.choice(["мягко", "аккуратно", "приятно", "нежно", "ласково"])
        how_stroke = random.choice(["легонько", "мягко", "аккуратно", "приятно"])
        if len(targets) == 1:
            variants = [
                f"@{user} {how_pat} пат-патает {target} по голове",
                f"@{user} {how_pat} пат-патает {target} по головушке",
                f"@{user} {how_pat} делает пат-пат {target}",
                f"@{user} {how_stroke} гладит {target} по голове",
                f"@{user} {how_stroke} гладит по голове {target}",
                f"@{user} хорошенько так патает {target}!",

                f"@{user} {how_pat} треплет {target} по макушке",
                f"@{user} {how_stroke} поглаживает ушки {target}",
                f"@{user} проводит лапкой по голове {target} — пат-пат ^w^",
                # f"@{user} делает серию быстрых патов по голове {target}",
                f"@{user} аккуратно касается головы {target} кончиками пальцев — пат!",
                f"@{user} {how_pat} похлопывает {target} по головушке, как хорошего котика",
                # f"@{user} укладывает ладонь на голову {target} и слегка трясёт — пат-пат-пат",
                # f"@{user} {how_pat} гладит {target} так, что у того аж хвостиком завилял",
                f"@{user} взъерошивает макушку {target} и делает *пат-пат*",
                f"@{user} делает очень серьёзный, очень ответственный пат-пат {target}",
            ]
        else:
            variants = [
                f"@{user} {how_pat} пат-патает {target} по головам",
                f"@{user} {how_pat} пат-патает {target} по головушкам",
                f"@{user} {how_pat} делает пат-пат {target}",
                f"@{user} {how_stroke} гладит {target} по голове",
                f"@{user} {how_stroke} гладит по голове {target}",
                f"@{user} хорошенько так патает {target}!",
                f"@{user} устраивает массовые пат-паты для {target}",
                f"@{user} устраивает массовые поглаживания {target}",
                f"@{user} делает групповое поглаживание по головам {target}",
            ]
        return random.choice(variants)

    async def _no_target_reply(self, user: str) -> str | None:
        return random.choice([
            f'Чтобы кого-нибудь пат-патнуть, нужно указать цель! Например "!pat @Quantum075Bot"',
            f"@{user} хочет кого-то погладить, но, запутавшись в списке чаттерсов, начинает гладить воздух",
            f"@{user} вытягивает лапку для пат-пата… но кого?..",
            f"@{user} делает пат-пат никому. Очень трогательно, но маловато эффекта.",
        ])

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice([
            *f"@{user}, подожди, пожалуйста, {delay_to_seconds(delay)}, а то сейчас кому-нибудь лысину сделаешь своими поглаживаниями о:"*3,
            f"@{user}, осторожнее! Частые пат-паты могут вызвать эффект *супергладкой головы*!",
        ])

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"@{user} с важным видом гладит собственную голову",
                f"@{user} делает пат-пат себе же",
                f"Кажется, кому-то не хватает патов! Погладьте @{user} пожалуйста!",
            ]
        )

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice(
            [
                f"Правильно, боты тоже заслуживаниют поглаживаний ^w^",
                f"@{target} пат-пат тебя, коллега-бот <3",

                f"@{user}, протокол «PAT-PAT» активирован. Поглаживание бота — выполнено.",
                f"@{target} принимает поглаживания, отвечая на них дружелюбным *пик-пик*",
            ]
        )

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"*довольное мурчание* ^w^",
                f"уиии, пасипа за пат-пат >w<",

                f"*приподнимает ушки и машет хвостом* ещё!",
                f"*мигает светодиодами от удовольствия* ^///^",
            ]
        )

