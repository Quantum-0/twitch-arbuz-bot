"""Модерационные команды канала: ``!бан``/``!ban`` и ``!разбан``/``!unban``.

Бан на конкретном канале (scope=channel) на все зрительские функции
(чат-бот, мемкоины, ИИ-стикеры, TTS). Право использования — стример канала
и его Twitch-модераторы (по бейджам). Срок — опциональным токеном
(``30м``/``2ч``/``3д``/``1нед``), остальное — причина. Снять канальный бан
может только владелец канала этими же командами или через панель ``/bans``.
"""

import logging
from datetime import datetime, timedelta
from time import time

from opentelemetry import trace

from database.models import TwitchUserSettings, User
from schemas.enums import VIEWER_BAN_FEATURES, BanScope, ChatterRole
from schemas.twitch import ChatMessageWebhookEventSchema
from services.bans import MAX_BAN_DURATION, BanService, looks_like_duration, parse_ban_duration
from twitch.chat.base.base_command import Command
from twitch.client.twitch import Twitch
from twitch.state_manager import SMParam, StateManager
from utils.chat_roles import KNOWN_BOTS, classify_chatter

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_MODERATION_COMMAND_COOLDOWN = 3  # сек, per-user защита от спама командой


def _format_duration(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds()) // 60
    days, hours, minutes = total_minutes // 1440, total_minutes % 1440 // 60, total_minutes % 60
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes and not days:
        parts.append(f"{minutes}м")
    return " ".join(parts) or "меньше минуты"


def _is_moderator(message: ChatMessageWebhookEventSchema) -> bool:
    return classify_chatter(message.badges, message.chatter_user_login) in (
        ChatterRole.STREAMER,
        ChatterRole.MODERATOR,
    )


class BanCommand(Command):
    command_name = "ban"
    command_aliases = ["бан", "ban"]
    command_description = "Бан зрителя на канале (только для стримера и модераторов)"

    cooldown_timer_per_user = _MODERATION_COMMAND_COOLDOWN

    def __init__(self, sm: StateManager, send_message, db_session_factory=None):
        super().__init__(sm, send_message, db_session_factory)
        from container_runtime import get_container

        self._twitch: Twitch = get_container().twitch()
        self._bans: BanService = get_container().bans()

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def _extract_duration(
        self,
        streamer: User,
        issuer: str,
        parts: list[str],
    ) -> tuple[timedelta | None, int] | None:
        """Срок бана и индекс начала причины.

        :return: ``(duration, reason_start)`` (duration=None — перманентный);
            ``None`` — токен не распознан, подсказка уже отправлена в чат.
        """
        if len(parts) <= 2:
            return (None, 2)
        parsed = parse_ban_duration(parts[2])
        if parsed is not None:
            if parsed > MAX_BAN_DURATION:
                await self.send_response(
                    chat=streamer,
                    message=f"@{issuer}, слишком большой срок — максимум {_format_duration(MAX_BAN_DURATION)}",
                )
                return None
            return (parsed, 3)
        if looks_like_duration(parts[2]):
            # Пользователь явно хотел срок (напр. «2ч24м»), но он не парсится —
            # не применяем перманентный бан молча, а подсказываем формат.
            await self.send_response(
                chat=streamer,
                message=(
                    f"@{issuer}, не удалось разобрать срок «{parts[2]}» — укажите одним числом, "
                    f"например 24ч, 30м, 3д или 1нед"
                ),
            )
            return None
        return (None, 2)

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema):
        await super().handle(streamer, message)
        issuer = message.chatter_user_name

        if not _is_moderator(message):
            await self.send_response(
                chat=streamer, message=f"@{issuer}, банить могут только стример и модераторы канала"
            )
            return

        # Лёгкий per-user кулдаун (модерационная команда, спамить незачем).
        last_call = await self._state_manager.get_state(
            channel=streamer.login_name,
            user=message.chatter_user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN,
        )
        if last_call and time() - last_call < self.cooldown_timer_per_user:
            return
        await self._state_manager.set_state(
            channel=streamer.login_name,
            user=message.chatter_user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN,
            value=time(),
        )

        parts = message.message.text.split()
        if len(parts) < 2 or not parts[1].strip("@"):
            await self.send_response(chat=streamer, message="Использование: !бан <логин> [срок] [причина]")
            return

        target_login = parts[1].strip("@").lower()
        if target_login == streamer.login_name.lower():
            await self.send_response(chat=streamer, message="Нельзя забанить стримера на его же канале O//O")
            return
        # if target_login in KNOWN_BOTS:
        #     await self.send_response(chat=streamer, message="Нельзя забанить бота хЪ")
        #     return

        extracted = await self._extract_duration(streamer, issuer, parts)
        if extracted is None:
            return
        duration, reason_start = extracted
        reason = " ".join(parts[reason_start:]).strip() or None

        target = await self._twitch.get_bot_user_by_login(target_login)
        if target is None:
            await self.send_response(chat=streamer, message=f"Не нашёл пользователя {target_login} в Twitch >.<")
            return

        expires_at = datetime.utcnow() + duration if duration is not None else None
        await self._bans.issue(
            target_twitch_id=str(target.id),
            target_login=target.login,
            target_name=target.display_name,
            features=VIEWER_BAN_FEATURES,
            scope=BanScope.CHANNEL,
            channel_twitch_id=streamer.twitch_id,
            channel_login=streamer.login_name,
            reason=reason,
            expires_at=expires_at,
            created_by_twitch_id=str(message.chatter_user_id),
            created_by_login=message.chatter_user_login,
            created_by_role="chat",
        )
        logger.info(
            "Chat ban issued: %s on channel %s by %s (expires_at=%s, reason=%s)",
            target.login,
            streamer.login_name,
            message.chatter_user_login,
            expires_at,
            reason,
        )
        term = f" на {_format_duration(duration)}" if duration is not None else " навсегда"
        tail = f" — {reason}" if reason else ""
        await self.send_response(chat=streamer, message=f"{target.display_name} забанен{term}{tail}")


class UnbanCommand(Command):
    command_name = "unban"
    command_aliases = ["разбан", "unban"]
    command_description = "Снять бан зрителя на канале (только для стримера и модераторов)"

    cooldown_timer_per_user = _MODERATION_COMMAND_COOLDOWN

    def __init__(self, sm: StateManager, send_message, db_session_factory=None):
        super().__init__(sm, send_message, db_session_factory)
        from container_runtime import get_container

        self._twitch: Twitch = get_container().twitch()
        self._bans: BanService = get_container().bans()

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema):
        await super().handle(streamer, message)
        issuer = message.chatter_user_name

        if not _is_moderator(message):
            await self.send_response(
                chat=streamer, message=f"@{issuer}, разбанивать могут только стример и модераторы канала"
            )
            return

        parts = message.message.text.split()
        if len(parts) < 2 or not parts[1].strip("@"):
            await self.send_response(chat=streamer, message="Использование: !разбан <логин>")
            return

        target_login = parts[1].strip("@").lower()
        target = await self._twitch.get_bot_user_by_login(target_login)
        if target is None:
            await self.send_response(chat=streamer, message=f"Не нашёл пользователя {target_login} в Twitch >.<")
            return

        removed = await self._bans.revoke_channel_bans(str(target.id), streamer.twitch_id)
        if removed:
            logger.info(
                "Chat unban: %s on channel %s by %s",
                target_login,
                streamer.login_name,
                message.chatter_user_login,
            )
            await self.send_response(chat=streamer, message=f"{target.display_name} разбанен")
        else:
            await self.send_response(chat=streamer, message=f"У {target.display_name} не было банов на этом канале")
