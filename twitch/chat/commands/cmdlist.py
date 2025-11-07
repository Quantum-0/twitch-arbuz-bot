from database.models import TwitchUserSettings, User
from twitch.chat.base.cooldown_command import SimpleCDCommand


class CmdlistCommand(SimpleCDCommand):
    cooldown_timer_per_chat = 120
    cooldown_timer_per_user = 600

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        return f"Список команд в чате для этого бота: https://bot.quantum0.ru/cmdlist?streamer={streamer.login_name}"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    command_name = "cmdlist"
    command_description = "Список команд чата"
    command_aliases = ["cmdlist", "cmd-list", "cmd_list"]

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True
