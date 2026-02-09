from database.database import AsyncSessionLocal
from database.models import TwitchUserSettings, User, RaidPasta
from twitch.chat.base.cooldown_command import SimpleCDCommand
import sqlalchemy as sa


class PastaCommand(SimpleCDCommand):
    command_name = "pasta"
    command_aliases = ["pasta", "паста", "рандомпаста", "пастарандом", "рандомнаяпаста"]
    command_description = "Сохранить, получить сохранённую или выдать рандомную пасту"

    cooldown_timer_per_chat = 3
    cooldown_timer_per_user = None

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pasta

    async def _handle(self, streamer: User, user: str, message: str) -> str | None:
        if any(pattern in message for pattern in {"рандомпаста", "пастарандом", "рандомнаяпаста", "рандом паста", "паста рандом"}):
            return await self._handle_random(streamer)
        elif message.strip() in {"!pasta", "!паста"}:
            return await self._get_pasta(streamer)
        elif streamer.login_name == user.lower():
            return await self._save_pasta(streamer, message.replace("!паста", "").replace("!pasta", "").strip())
        return "Только владелец канала может сохранить новую пасту 👀"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""

    async def _handle_random(self, streamer: User) -> str | None:
        async with AsyncSessionLocal() as session:
            result = (await session.execute(
                sa.select(RaidPasta).order_by(sa.func.random()).limit(1)
            )).scalar_one_or_none()
            return result.text

    async def _get_pasta(self, streamer: User) -> str | None:
        return streamer.settings.personal_pasta or "Паста не сохранена."

    async def _save_pasta(self, streamer: User, pasta: str) -> str:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa.update(TwitchUserSettings).where(TwitchUserSettings.user_id == streamer.id).values({"personal_pasta": pasta})
            )
            await session.commit()
        return "Паста сохранена."