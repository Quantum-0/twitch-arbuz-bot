import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.utils import delay_to_seconds, join_targets


class BiteCommand(SimpleTargetCommand):
    command_name = "bite"
    command_aliases = ["bite", "кусь", "кусьб", "укусить", "куснуть"]
    command_description = "Укусить пользователя чата"

    need_target = True
    cooldown_timer = 45
    cooldown_count = 2

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_bite

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        kind_of_bite = [
            "злобный",
            "приятный",
            "мягкий",
            "нежный",
            "аккуратный",
            "агрессивный",
            "коварный",
            "шаловливый",
            "страстный",
            "игривый",
            "робкий",
            "ленивый",
            "озорной",
            "задумчивый",
            "шутливый",
            "подлый",
        ]
        bite_verb = [
            *["делает"]*4,
            "совершает",
            "устраивает",
            "выполняет",
        ]
        target_to_bite = [
            "левое ухо",
            "правое ухо",
            "пятку",
            "хвост",
            "ногу",
            "пэрсики",
            "нос",
            "плечо",
            "жёпку",
            "палец",
            "животик",
            "локоть",
            "коленку",
            "пальчик",
            "бочок",
            "шею",
        ]
        target = join_targets(targets)
        if len(targets) == 1 and random.random() < 0.05:
            return f"@{user} жадно впивается в руку {target} своими острыми зубками (•ิ_•ิ)"
        # TODO: кусает, делает кусь, кусявкает, покусывает?
        return f"@{user} {random.choice(bite_verb)} {random.choice(kind_of_bite)} кусь {target} за {random.choice(target_to_bite)}"

    async def _no_target_reply(self, user: str) -> str | None:
        return random.choice([
            *[f'Чтобы укусить кого-то, нужно указать цель. Например: "!кусь @{user}"']*4,
            f"@{user}, кусь в пустоту — это, конечно, мило, но лучше укажи жертву!",
        ])

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
       return random.choice([
            f"@{user}, твои зубки находятся в перезарядке! Подожди чуть-чуть, прежде чем пользоваться командой снова.",
            f"@{user}, твои зубки устали кусаться, подожди {delay_to_seconds(delay)}, прежде чем делать новый кусь!",
            f"@{user}, твои зубки отдыхают! Подожди немного перед новым укусом.",
            f"Зубки @{user} заряжаются… Жди {delay_to_seconds(delay)}!",
            f"@{user}, твой последний кусь был слишком силён и коварен, зубкам требуется перезарядка.",
            f"@{user}, ещё чуть-чуть терпения — и можно будет снова кусаться!",
        ])

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice([
            f"@{user} кусает сам себя о.О",
            f"@{user} совершает САМОКУСЬ!",

            f"@{user} решает проверить вкус собственных пальцев 🤔",
            f"Самокусь?! @{user}, ты чего? Тебя давно не кормили? :с",
            f"@{user} в ярости — кусает себя за ухо! Как? Остаётся загадкой..",
            # f"Это было больно, но весело: @{user} кусает самого себя.",
            f"Самокусь активирован. @{user} удивлён, но доволен.",
        ])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([
            f"{target} простите за беспокойство, коллега-бот, но пользователь @{user} делает вам кусьб",
            f"За что вы бота кусаете, он и так работает 24/7, несчастный т_т",
        ])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([
            f"@{user}, а меня то за что?!",
            f"Меня кусать нельзя, кусай кого-нибудь другого!",
            f"Ну капец, уже на ботов своими зубами нападают..",
            f"@{user}, щас как сам тебя укушу >:c Банхамером!!!",
        ])
