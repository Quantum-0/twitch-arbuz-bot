import re

from database.models import TwitchUserSettings, User, RaidPasta
from twitch.chat.base.cooldown_command import SimpleCDCommand
import sqlalchemy as sa


class LinkDisCommand(SimpleCDCommand):
    command_name = "dis"
    command_aliases = ["ds", "dis", "дис", "дс", "дискорд", "discord"]
    command_description = "Сохранить, получить сохранённую или выдать ссылку на дискорд-сервер стримера"

    cooldown_timer_per_chat = 5
    cooldown_timer_per_user = 10

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_ds_link

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        if len(message.strip().split()) == 1:
            return await self._get_link(streamer)
        elif streamer.login_name == user.lower():
            link = message.strip().split(maxsplit=1)[1]
            parsed = re.match(r"(https?://)?(www\.)?(discord\.(gg|io|me|li)|discordapp\.com/invite|discord\.com/invite)/[^\s/]+?", link)
            if not parsed:
                return "Кажется это некорректная ссылка :с"
            await self._save_link(streamer, link)
            return "Ссылка сохранена!"
        return "Только владелец канала может сохранить ссылку на дис 👀"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""

    @staticmethod
    async def _get_link(streamer: User) -> str:
        if streamer.settings.ds_link:
            return streamer.settings.ds_link
        return "Ссылка на Discord-сервер не указана."

    async def _save_link(self, streamer: User, link: str) -> None:
        async with self.db_session() as session:
            await session.execute(
                sa.update(TwitchUserSettings).where(TwitchUserSettings.user_id == streamer.id).values({"ds_link": link})
            )
            await session.commit()
