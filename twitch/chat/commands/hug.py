import random
from time import time

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand
from twitch.state_manager import SMParam
from twitch.utils import delay_to_seconds, join_targets


class HugCommand(SimpleTargetCommand):
    command_name = "hug"
    command_aliases = ["hug", "hugs", "обнять", "обнимать"]
    command_description = "Заобнимать чаттерса!"

    need_target = True
    cooldown_timer = 25

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_hug

    async def _handle(self, streamer: User, user: str, message: str, targets: list[str]) -> str:
        target = join_targets(targets)
        join_to_hugs_str = ""
        if len(targets) == 1:
            assert isinstance(target, str)
            last_hug_target = await self._state_manager.get_state(
                channel=streamer.login_name,
                user=target[1:].lower(),
                command=self.command_name,
                param=SMParam.LAST_APPLY,
            )
            if last_hug_target and time() - last_hug_target < 20:
                join_to_hugs_str = "присоединяется к обнимашкам и "
        variants = [
            f"@{user} {join_to_hugs_str}обнимает {target}",
            f"@{user} {join_to_hugs_str}крепко обнимает {target}",
            f"@{user} {join_to_hugs_str}набрасывается с объятиями на {target}",
            f"@{user} {join_to_hugs_str}стискивает в объятиях {target}",
            f"@{user} {join_to_hugs_str}заобнимовывает {target}",
            f"@{user} {join_to_hugs_str}окутывает {target} мягкими и тёплыми объятиями",
            f"@{user} {join_to_hugs_str}подходит к {target} и аккуратно, но уверенно обнимает",
            f"@{user} {join_to_hugs_str}обнимает {target} так сильно, что аж искорки в воздухе!",
            f"@{user} {join_to_hugs_str.replace(' и ', ', ')}подбегает и с разбегу влетаeт в мягкие обнимашки с {target}",
            # f"@{user} {join_to_hugs_str}дарит {target} объятия уровня: «ммм, дааа»",
            # f"@{user} {join_to_hugs_str}накрывает {target} тёплым одеялком из объятий",
        ]
        return random.choice(variants)

    async def _no_target_reply(self, user: str) -> str | None:
        return random.choice(
            [
                f"@{user} хочет обнимашек, но не справляется с выбором цели для этого, поэтому обнимает плюшевую акулку",
                # f"@{user} вытягивает руки для обнимашки… но никого рядом нет… эх…",
                f"@{user} жаждет обнимашек, но за неимением цели обнимает табуретку",
                f"@{user} так хочет обнять кого-то, что случайно обнимает ближайшей кактус. Ай. 🌵",
            ]
        )

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice(
            [
                f"@{user} подожди, секундочку, прежде чем обнимать кого-то другого!",
                f"@{user}, вы пока что в процессе обнимания другого пользователя! Подождите {delay_to_seconds(delay)}",
            ]
        )

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice(
            [
                # f"@{user} обхватывает себя руками",
                f"@{user} испытывает тактильный голод, из-за чего пытается обнимать себя. Обнимите @{user}, пожалуйста!",
                f"Awww, @{user}, ну ты чего? Давай хотя бы я тебя обниму o^o !hug @{user}",
                f"@{user} обнимает плюшевую акулу из Икеи",
            ]
        )

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice(
            [
                f"Обнимаем боооота! >w<",
                f"Боты тоже заслуживают обнимашек! Обнимаем @{target}!",
                f"@{target} с серьёзным видом инициирует протокол «мягкие объятия» для @{user}",
                f"@{target} принимает обнимашки и воспроизводит довольное электронное мурчание",
            ]
        )

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([f"Уиии, пасиба за обнимашки!", f"@{user}, обнимаю тебя в ответ! <3"])
