from database.models import TwitchUserSettings, User
from twitch.chat.base.cooldown_command import SimpleCDCommand


class SteamCommand(SimpleCDCommand):
    command_name = "steam"
    command_aliases = ["steam", "стим"]
    command_description = "Ссылка на Steam-профиль стримера (если привязан)"

    cooldown_timer_per_chat = 5
    cooldown_timer_per_user = 10

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_steam_link

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        if streamer.links.steam:
            return f"Steam: {streamer.links.steam}"
        return "Steam не привязан :с"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""
