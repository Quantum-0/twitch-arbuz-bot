from collections.abc import Callable
from typing import Awaitable

from twitchAPI.chat import ChatMessage

from database.models import TwitchUserSettings
from twitch.command import Command
from twitch.state_manager import StateManager


class CommandsManager:
    def __init__(self, storage: StateManager, send_message: Callable[..., Awaitable[None]]):
        self.commands: list[Command] = []
        self._sm = storage
        self._send_message = send_message

    def register(self, command: type[Command]):
        self.commands.append(command(self._sm, self._send_message))

    async def handle(self, user_settings: TwitchUserSettings, channel: str, message: ChatMessage):
        for cmd in self.commands:
            if not cmd.is_enabled(user_settings):
                continue
            if any(message.text.startswith(x) for x in ["!" + alias for alias in cmd.command_aliases]):
                await cmd.handle(channel, message)