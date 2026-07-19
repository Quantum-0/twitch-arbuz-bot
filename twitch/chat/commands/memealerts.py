from database.models import TwitchUserSettings, User
from twitch.chat.base.cooldown_command import SimpleCDCommand


class MemealertsLinkCommand(SimpleCDCommand):
    command_name = "memealerts"
    command_aliases = [
        "meme",
        "memealerts",
        "memalerts",
        "мемы",
        "мемалёртс",
        "мемалёртсы",
        "мемалертс",
        "мемалерты",
        "мемалёрт",
        "мемалёрты",
        "мемалерт",
        "мемеалёртс",
        "мемеалёрты",
        "мемеалертс",
        "мемеалерты",
    ]
    command_description = "Ссылка на мемалёртсы стримера (если интеграция подключена)"

    cooldown_timer_per_chat = 5
    cooldown_timer_per_user = 10

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        # Команда доступна только если стример включил её в панели управления
        return streamer_settings.enable_memealerts_link

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        ma = streamer.memealerts
        integration_enabled = bool(ma and (ma.memealerts_reward or ma.access_token))
        link = streamer.settings.memealerts_link
        if not integration_enabled:
            return "Интеграция с Memealerts не подключена :с"
        if not link:
            return "Ссылка на Memealerts не указана."
        return f"Memealerts: https://memealerts.com/{link}"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None
