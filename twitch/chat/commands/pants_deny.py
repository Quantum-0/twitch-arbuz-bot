import logging
from collections.abc import Awaitable, Callable

import sqlalchemy as sa
from opentelemetry import trace
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TwitchUserSettings, User, PantsDeny
from twitch.chat.base.cooldown_command import SimpleCDCommand
from twitch.state_manager import StateManager
from twitch.utils import delay_to_seconds

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class PantsDenyCommand(SimpleCDCommand):
    command_name = "pants_deny"
    command_aliases = ["запретитьтрусы", "запреттрусов"]  # , "разрешитьтрусы"]
    command_description = "Ограничение доступа другим пользователю к применению к вам команды !трусы"

    cooldown_timer_per_chat = 1
    cooldown_timer_per_user = 600

    def __init__(
        self,
        sm: StateManager,
        send_message: Callable[..., Awaitable[None]],
        db_session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        super().__init__(sm, send_message, db_session_factory)

    async def check_denied(self, *names: str) -> list[str]:
        async with self.db_session() as session:
            result = await session.execute(
                sa.select(PantsDeny)
                .where(PantsDeny.name.in_([n.lower() for n in names]))
            )
            return result.scalars().all()

    async def add_to_denied(self, name: str):
        async with self.db_session() as session:
            await session.execute(
                insert(PantsDeny)
                .values({"name": name.lower()})
            )
            await session.commit()

    async def remove_from_denied(self, name: str):
        async with self.db_session() as session:
            await session.execute(
                sa.delete(PantsDeny)
                .where(PantsDeny.name == name.lower())
            )
            await session.commit()

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pants

    async def _handle(self, streamer: User, user: str, message: str) -> str | None:
        logger.info("Handle deny raffle")
        user_is_denied = bool(await self.check_denied(user))
        logger.info(f"User denied = {user_is_denied}")

        if not user_is_denied:
            await self.add_to_denied(user)
            logger.info(f"User {user} deny to raffle their pants")
            return f"Отныне @{user} запрещает использовать свои трусы для розыгрыша!"

        await self.remove_from_denied(user)
        logger.info(f"User {user} allows to raffle their pants")
        return f"@{user} вновь разрешает использовать свои трусы для розыгрыша!"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return (f"Нельзя так часто пользоваться командой запрета трусов ^w^ "
                f"Чтоб снова {'разрешить' if bool(await self.check_denied(user)) else 'запретить'}"
                f"свои трусы для розыгрыша, подождите {delay_to_seconds(delay)} хЪ")
