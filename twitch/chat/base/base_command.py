from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TwitchUserSettings, User
from routers.schemas import ChatMessageWebhookEventSchema
from twitch.state_manager import StateManager


class Command(ABC):
    @property
    @abstractmethod
    def command_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def command_description(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def command_aliases(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        raise NotImplementedError

    def __init__(
        self,
        sm: StateManager,
        send_message: Callable[..., Awaitable[None]],
        db_session_factory: Callable[[], AsyncSession] | None = None,
    ):
        self._state_manager = sm
        self.send_response = send_message
        self._db_session_factory = db_session_factory
        if len(self.command_aliases) == 0:
            raise RuntimeError("Command has no trigger aliases")

        from container_runtime import get_container

        self.chat_bot = get_container().chat_bot()

    def _get_db_session_factory(self) -> Callable[[], AsyncSession]:
        if self._db_session_factory is None:
            raise RuntimeError("DB session factory was not configured for command")
        return self._db_session_factory

    @asynccontextmanager
    async def db_session(self) -> AsyncIterator[AsyncSession]:
        async with self._get_db_session_factory()() as session:
            yield session

    @abstractmethod
    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema):
        raise NotImplementedError
