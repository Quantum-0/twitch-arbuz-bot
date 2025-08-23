import logging
from collections.abc import Callable
from typing import Awaitable

from twitchAPI.chat import ChatMessage

from database.models import TwitchUserSettings
from twitch.base_commands import Command
from twitch.state_manager import StateManager


logger = logging.getLogger(__name__)


class CommandsManager:
    def __init__(self, storage: StateManager, send_message: Callable[..., Awaitable[None]]):
        self.commands: list[Command] = []
        self._sm = storage
        self._send_message = send_message

    def register(self, command: type[Command]):
        self.commands.append(command(self._sm, self._send_message))
        logger.info(f"Command {command} was registered")

    async def handle(self, user_settings: TwitchUserSettings, channel: str, message: ChatMessage):
        logger.debug(f"Handling message with {self}")
        for cmd in self.commands:
            if not cmd.is_enabled(user_settings):
                continue
            # Обработка реплаев
            if message.reply_parent_display_name and message.text.startswith(f"@{message.reply_parent_display_name} "):
                message.text = message.text[len(message.reply_parent_display_name) + 2:]

            if any(message.text.lower().startswith(x) for x in ["!" + alias for alias in cmd.command_aliases]):
                logger.debug(f"Handler for command was found: {cmd}")
                await cmd.handle(channel, message)

    async def get_commands_of_user(self, user) -> list[tuple[str, str, str]]:
        user_settings: TwitchUserSettings = user.settings
        result = []
        for cmd in self.commands:
            if cmd.is_enabled(user_settings):
                result.append((cmd.command_name, ', '.join(["!" + cmd for cmd in cmd.command_aliases]), cmd.command_description))
        return result
