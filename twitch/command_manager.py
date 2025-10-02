import logging
from collections.abc import Callable
from typing import Awaitable

from database.models import TwitchUserSettings, User
from routers.schemas import ChatMessageWebhookEventSchema
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

    async def handle(self, user_settings: TwitchUserSettings, streamer: User, message: ChatMessageWebhookEventSchema):
        logger.debug(f"Handling message with {self}")
        for cmd in self.commands:
            if not cmd.is_enabled(user_settings):
                continue
            # Обработка реплаев
            if message.reply and message.reply.parent_user_name and message.message.text.startswith(f"@{message.reply.parent_user_name} "):
                message.message.text = message.message.text[len(message.reply.parent_user_name) + 2:]

            if (
                any(
                    # текст сообщения начинается с "!cmd " или = "!cmd"
                    (message.message.text.lower().startswith(x + " ") or message.message.text.lower() == x)
                    for x
                    in [f"!{alias}" for alias in cmd.command_aliases]
                )
            ):
                logger.debug(f"Handler for command was found: {cmd}")
                await cmd.handle(streamer, message)

    async def get_commands_of_user(self, user) -> list[tuple[str, str, str]]:
        user_settings: TwitchUserSettings = user.settings
        result = []
        for cmd in self.commands:
            if cmd.is_enabled(user_settings):
                result.append((cmd.command_name, ', '.join(["!" + cmd for cmd in cmd.command_aliases]), cmd.command_description))
        return result
