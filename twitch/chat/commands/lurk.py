import random
from time import time

from database.models import TwitchUserSettings, User
from twitch.chat.base.cooldown_command import SimpleCDCommand


class LurkCommand(SimpleCDCommand):
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    cooldown_timer_per_user = 30
    cooldown_timer_per_chat = None

    command_name = "lurk"
    command_aliases = ["lurk", "unlurk", "лурк", "анлурк"]
    command_description = "Сообщить стримеру и чатику, что вы уходите в лурк или возвращаетесь из него"

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_lurk

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        state: bool = not ("unlurk" in message or "анлурк" in message)
        previous_state: bool = (
            await self._state_manager.get_state(
                channel=streamer.login_name,
                user=user.lower(),
                command=self.command_name,
            )
            is not None
        )

        if state == previous_state and state is True:
            return f"@{user}, ты и так уже в лурке"

        if state and not previous_state:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                user=user.lower(),
                command=self.command_name,
                value=time(),
            )
            variants = [
                f"@{user} прячется за холодильник и наблюдает за стримом оттуда. Спасибо за лурк!",
                f"@{user} спотыкается об камушек, падает и проваливается в лурк",
                f"У @{user} появились более важные дела, чем просмотр этого стрима, представляете?!",
                f"@{user} превращается в крокодила, погружается в ближайшую лужу, и теперь оттуда торчат только глазки 👀",
                f"@{user} медленно растворяется в воздухе, оставляя после себя только шорох… *лурк активирован*.",
                f"@{user} превращается в маленькое облачко тумана и ускользает в режим лурка.",
                f"@{user} на цыпочках удаляется в угол и включает режим скрытого наблюдения.",
                f"@{user} надевает шляпу-невидимку и скрывается в тени. Лурк!",
                f"@{user} заныривает под одеяло, выглядывая одним глазом. Спасибо за лурк!",
                f"@{user} телепортируется в кусты. Эти самые… виртуальные.",
                f"@{user} укутывается в плед-кокон и тихо наблюдает за стримом оттуда.",
                f"@{user} уходит чинить какую-то очень важную штуку, но слышит стрим фоном.",
                f"@{user} становится гифкой «loading…» и исчезает в лурке.",
                f"@{user} превращается в камень. Но такой… наблюдающий камень.",
                f"@{user} забирается на шкаф. Оттуда и луркает. Стратегически.",
                f"@{user} активирует режим AFK + наблюдение. Спасибо за лурк!",
                f"@{user} ныряет под кровать, где живёт монстр-луркер. Теперь они луркают вдвоём.",
                f"@{user} тихо падает с кресла и решает остаться внизу луркать.",
                f"@{user} забирается на люстру и висит там, наблюдая за чатом как летучая мышь.",
                f"Внимание, чат! Важное объявление! Пользователю @{user} нужно пойти покакать! (Ладно, на самом деле, что бы Вы не делали, удачки с делами! <3)",
            ]
            return random.choice(variants)

        if previous_state and not state:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                user=user.lower(),
                command=self.command_name,
                value=None,
            )
            unlurk_variants = [
                f"@{user} выплывает из лурка. С возвращением!",
                f"@{user} возвращается из тени и снова с нами!",
                f"@{user} выбегает обратно в чат, запыхавшись. Добро пожаловать!",
                f"@{user} снимает плащ-невидимку. Привет обратно!",
                f"@{user} снова материализуется в чате!",
                f"@{user} громко заявляет: «Я вернулся!»",
                f"@{user} выпадает из кармана стримера и перестаёт луркать.",
                f"@{user} возвращается с печеньем. Сам луркал — и нам принёс!",
                f"@{user} восстаёт из глубин лурка, как древний дух.",
                f"@{user} снова активен в чате — лурк окончен!",
                f"@{user} вылезает из кустов и присоединяется к болтовне.",
                f"@{user} внезапно сваливается с люстры, прямо на стол перед стримером!",
            ]
            return random.choice(unlurk_variants)
