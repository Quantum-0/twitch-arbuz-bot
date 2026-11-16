import logging
from collections.abc import Awaitable, Callable

from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TwitchUserSettings, User
from schemas.api import StatsType
from schemas.twitch import ChatMessageWebhookEventSchema
from services.statistics import StatisticsService
from twitch.chat.base.base_command import Command
from twitch.state_manager import StateManager

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def _text_without_reply_prefix(message: ChatMessageWebhookEventSchema) -> str:
    """Текст сообщения без префикса реплая «@имя » (Twitch вставляет его в text реплая)."""
    if (
        message.reply
        and message.reply.parent_user_name
        and message.message.text.startswith(f"@{message.reply.parent_user_name} ")
    ):
        return message.message.text[len(message.reply.parent_user_name) + 2 :]
    return message.message.text


class CommandsManager:
    def __init__(
        self,
        storage: StateManager,
        send_message: Callable[..., Awaitable[None]],
        db_session_factory: Callable[[], AsyncSession] | None = None,
        statistics: StatisticsService | None = None,
    ):
        self.commands: list[Command] = []
        self._aliases: set[str] = set()
        self._sm = storage
        self._send_message = send_message
        self._db_session_factory = db_session_factory
        self._statistics = statistics

    def register(self, command: type[Command]):
        cmd = command(self._sm, self._send_message, self._db_session_factory)
        self.commands.append(cmd)
        self._aliases.update(f"!{alias}".lower() for alias in cmd.command_aliases)
        logger.info(f"Command {command} was registered")

    async def is_candidate(self, message: ChatMessageWebhookEventSchema) -> bool:
        """Префильтр до загрузки стримера из БД: начинается ли сообщение с алиаса
        какой-то из зарегистрированных команд. ``enable_*`` стримера тут неизвестен —
        проверяется позже (промах по выключенной команде допустим, см. on_message).
        """
        first_token = _text_without_reply_prefix(message).lower().split(" ", 1)[0]
        return first_token in self._aliases

    @tracer.start_as_current_span("ChatBot: Command Manager: Handle")
    async def handle(
        self,
        user_settings: TwitchUserSettings,
        streamer: User,
        message: ChatMessageWebhookEventSchema,
    ):
        logger.debug(f"Handling message with {self.__class__.__name__}")
        if not user_settings.allow_shared_chat and message.source_broadcaster_user_id:
            logger.debug(
                f"Skip message because of common chat. Source: {message.source_broadcaster_user_login}, Broadcaster: {message.broadcaster_user_login}"
            )
            return
        for cmd in self.commands:
            if not cmd.is_enabled(user_settings):
                continue
            # Обработка реплаев
            message.message.text = _text_without_reply_prefix(message)

            if any(
                # текст сообщения начинается с "!cmd " или = "!cmd"
                (message.message.text.lower().startswith(x + " ") or message.message.text.lower() == x)
                for x in [f"!{alias}" for alias in cmd.command_aliases]
            ):
                logger.info(f"Handler for command was found: {cmd.__class__.__name__}")
                await cmd.handle(streamer, message)
                if self._statistics is not None:
                    self._statistics.inc(StatsType.COMMAND_HANDLED, subtype=cmd.command_name)

    async def get_commands_of_user(self, user) -> list[tuple[str, str, str]]:
        user_settings: TwitchUserSettings = user.settings
        result = []
        for cmd in self.commands:
            if cmd.is_enabled(user_settings):
                result.append(
                    (
                        cmd.command_name,
                        ", ".join(["!" + cmd for cmd in cmd.command_aliases]),
                        cmd.command_description,
                    )
                )
        return result
