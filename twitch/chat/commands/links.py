from database.models import TwitchUserSettings, User
from twitch.chat.base.cooldown_command import SimpleCDCommand


class LinksCommand(SimpleCDCommand):
    command_name = "links"
    command_aliases = ["links", "ссылки", "соцсети", "socials"]
    command_description = "Список всех сохранённых ссылок стримера"

    cooldown_timer_per_chat = 300
    cooldown_timer_per_user = 60

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_links_command

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        return await self._build_links_list(streamer)

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""

    @staticmethod
    async def _build_links_list(streamer: User) -> str:
        links = streamer.links
        parts: list[str] = []
        settings = streamer.settings

        if settings.enable_tg_link and links.telegram:
            parts.append(f"Telegram: t.me/{links.telegram}")
        if settings.enable_ds_link and links.discord:
            parts.append(f"Discord: {links.discord}")
        if settings.enable_tiktok_link and links.tiktok:
            parts.append(f"TikTok: tiktok.com/@{links.tiktok}")
        if settings.enable_youtube_link and links.youtube:
            parts.append(f"YouTube: youtube.com/{links.youtube}")

        if not parts:
            return "Ссылки не указаны."

        return "Ссылки: " + " | ".join(parts)
