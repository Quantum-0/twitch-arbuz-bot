import re

import sqlalchemy as sa

from database.models import Links, TwitchUserSettings, User
from twitch.chat.base.cooldown_command import SimpleCDCommand


class LinkYoutubeCommand(SimpleCDCommand):
    command_name = "youtube"
    command_aliases = ["youtube", "ютуб", "ют", "yt"]
    command_description = "Сохранить, получить сохранённую или выдать ссылку на ютуб-канал стримера"

    cooldown_timer_per_chat = 5
    cooldown_timer_per_user = 10

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_youtube_link

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        if len(message.strip().split()) == 1:
            return await self._get_link(streamer)
        if streamer.login_name == user.lower():
            link = message.strip().split(maxsplit=1)[1]
            parsed = re.match(
                r"(https?:\/\/)?(www\.)?(youtube\.com\/(?P<channel>(c\/|@|channel\/)?[\w\-\.]+)|youtu\.be\/(?P<short>[\w\-]+))",
                link,
            )
            if not parsed:
                return "Кажется это некорректная ссылка :с"
            clean_link = parsed.groupdict().get("channel") or parsed.groupdict().get("short") or None
            if not clean_link:
                return "Кажется это некорректная ссылка :с"
            await self._save_link(streamer, clean_link)
            return "Ссылка сохранена!"
        return "Только владелец канала может сохранить ссылку на ютуб 👀"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""

    @staticmethod
    async def _get_link(streamer: User) -> str:
        if streamer.links.youtube:
            return "youtube.com/" + streamer.links.youtube
        return "Ссылка на YouTube не указана."

    async def _save_link(self, streamer: User, link: str) -> None:
        async with self.db_session() as session:
            await session.execute(sa.update(Links).where(Links.user_id == streamer.id).values({"youtube": link}))
            await session.commit()
