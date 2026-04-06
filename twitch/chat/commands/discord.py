import re

from database.models import TwitchUserSettings, User, RaidPasta
from twitch.chat.base.cooldown_command import SimpleCDCommand
import sqlalchemy as sa


class LinkDisCommand(SimpleCDCommand):
    command_name = "dis"
    command_aliases = ["ds", "dis", "дис", "дс", "дискорд", "discord"]
    command_description = "Сохранить, получить сохранённую или выдать ссылку на дискорд-сервер стримера"

    cooldown_timer_per_chat = 10
    cooldown_timer_per_user = 60

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_ds_link

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        if len(message.strip().split()) == 1:
            return await self._get_link(streamer)
        elif streamer.login_name == user.lower():
            link = message.strip().split(maxsplit=1)[1]
            # TODO: parse and check link
            # parsed = re.match(r"(@(?P<username1>\w*)|(https?:\/\/)?t\.me\/(?P<username2>\w*)(\/(?P<post>\d+))?|(https?:\/\/)?(?P<username3>\w*)\.t\.me\/?)", link)
            # clean_link = parsed.groupdict().get("username1") or parsed.groupdict().get("username2") or parsed.groupdict().get("username3") or None
            # if not clean_link:
            #     return "Кажется это некорректная ссылка :с"
            await self._save_link(streamer, link)
            return "Ссылка сохранена!"
        return "Только владелец канала может сохранить ссылку на дис 👀"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""

    @staticmethod
    async def _get_link(streamer: User) -> str:
        if streamer.settings.tg_link:
            return "t.me/" + streamer.settings.tg_link
        return "Ссылка на Discord-сервер не указана."

    async def _save_link(self, streamer: User, link: str) -> None:
        async with self.db_session() as session:
            await session.execute(
                sa.update(TwitchUserSettings).where(TwitchUserSettings.user_id == streamer.id).values({"ds_link": link})
            )
            await session.commit()
