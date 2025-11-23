import random

from database.models import TwitchUserSettings, User
from twitch.chat.base.target_command import SimpleTargetCommand


class BushCommand(SimpleTargetCommand):
    command_name = "bush"
    command_aliases = ["куст"]
    command_description = "Опечатка в команде !кусь"

    need_target = True
    cooldown_timer = 120
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_bite

    async def _handle(
        self, streamer: User, user: str, message: str, targets: list[str]
    ) -> str:
        variants = [
            'срывает ветку с куста и кладёт себе на голову, приговаривая "я цвяточек"',
            "запрыгивает в куст и начинает издавать звуки растительности",
            "превращается в куст " + random.choice(["можжевельника", "мяты", "малины"]),
            f"берёт куст, вытаскивает его из земли и швыряет в стримера",
            "отращивает на себе пару веточек и листочков на них",
            "начинает фотосинтезировать",

            "становится декоративным кустом и требует полива раз в сутки",
            "издаёт таинственное шуршание, хотя ветра нет…",
            "выпускает в воздух запах свежей зелени и гордится собой",
            "решает, что теперь живёт здесь, и пускает корни",
            "начинает размахивать ветками, изображая агрессивный куст",
            "делает кустовое *бдыщь* и осыпает всех листьями",
            "садится неподвижно и шепчет что-то на растительном",
            "распускает на себе несколько цветочков — чисто для красоты",
            "выпускает ягоды неизвестного происхождения и подозрительного вида",
            "становится кустом с глазами, которые слегка светятся в темноте",
            "тренирует искусство ниндзя-куста, пытаясь слиться с местностью",
            "расцветает и становится в два раза симпатичнее",
            "начинает тихо шуршать, словно обсуждает что-то с другими кустами",
            "становится кустом загадочного происхождения, который издаёт мягкое «буш-буш»",
        ]
        return (
            f"Опечатавшись в команде !кусь, @{user} внезапно {random.choice(variants)}"
        )

    async def _no_target_reply(self, user: str) -> str | None:
        return await self._handle(None, user, "", [])

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    async def _self_call_reply(self, user: str) -> str | None:
        return await self._handle(None, user, "", [])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return None

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return "Сам ты куст! О.О"
