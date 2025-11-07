from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

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

    def __init__(self, sm: StateManager, send_message: Callable[..., Awaitable[None]]):
        self._state_manager = sm
        self.send_response = send_message
        if len(self.command_aliases) == 0:
            raise RuntimeError("Command has no trigger aliases")

        from dependencies import get_chat_bot

        self.chat_bot = next(get_chat_bot())

    @abstractmethod
    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema):
        raise NotImplementedError
