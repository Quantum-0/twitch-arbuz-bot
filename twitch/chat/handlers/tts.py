"""TTSHandler — серверный фильтр/оркестратор TTS в on_message.

Решает, какие сообщения озвучивать (по матрице разрешений ролей × триггеров),
применяет КД на зрителя/канал, очистку текста, модерацию финального текста и
отправляет текст в SSE-канал TTS. Награда обрабатывается отдельно в
``TwitchEventSubService.reward_tts`` (т.к. для fulfil/cancel нужен redemption_id
из webhook'а, а в чат-сообщении награды ролей по badges мы отдельно не выделяем).
"""

from __future__ import annotations

import json
import logging
from time import time
from typing import TYPE_CHECKING

from opentelemetry import trace

from database.models import TwitchUserSettings, User
from schemas.enums import TTSTrigger
from schemas.twitch import ChatMessageWebhookEventSchema
from services.moderation import ModerationService
from services.sse_manager import SSEManager
from twitch.chat.handlers.handlers import CommonMessagesHandler, HandlerResult
from twitch.state_manager import SMParam
from utils.chat_roles import classify_chatter
from utils.enums import SSEChannel
from utils.tts import clean_tts_text, get_tts_settings, truncate_tts

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Префикс команды TTS.
TTS_COMMAND_PREFIX = "!!!"
# Имя «команды» в StateManager для КД.
_TTS_CD_COMMAND = "tts"


class TTSHandler(CommonMessagesHandler):
    COMMAND_NAME = "tts_handler"

    def __init__(
        self,
        sm,
        send_message: Callable[..., Awaitable[None]],
        db_session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        from container_runtime import get_container

        self._container = get_container()
        super().__init__(sm, send_message, db_session_factory)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        # TTS-включение живёт на user.tts, не на settings — здесь не можем проверить.
        # Проверяем внутри handle (есть доступ к streamer.tts). Не отключаем handler
        # целиком, чтобы простая смена тоггла не требовала перезапуска.
        return True

    @tracer.start_as_current_span("ChatBot: TTS Handler")
    async def handle(self, streamer: User, message: ChatMessageWebhookEventSchema) -> HandlerResult:
        tts = get_tts_settings(streamer)
        if not tts.enabled:
            return HandlerResult.SKIPED

        # Награду здесь не озвучиваем — её обрабатывает webhook (reward_tts).
        # Если это сообщение-награда (есть channel_points_custom_reward_id), skip'аем.
        if message.channel_points_custom_reward_id:
            return HandlerResult.SKIPED

        # 1. Оверлей подключён? (дёшево — без проверки дальше не идём)
        ssem: SSEManager = self._container.sse_manager()
        if not await ssem.has_clients(int(streamer.twitch_id), SSEChannel.TTS):
            return HandlerResult.SKIPED

        text_raw = message.message.text
        if not text_raw:
            return HandlerResult.SKIPED

        # 2. Роль чаттера.
        role = classify_chatter(message.badges, message.chatter_user_login)

        # 3. Триггер (старшинство).
        trigger = self._detect_trigger(message, text_raw)
        if trigger is None:
            return HandlerResult.SKIPED

        # 4. Матрица разрешений.
        perms: dict = tts.permissions or {}
        roles = perms.get("roles", {}) if isinstance(perms, dict) else {}
        role_perms = roles.get(role.value, {}) if isinstance(roles, dict) else {}
        if not role_perms.get(trigger.value, False):
            return HandlerResult.SKIPED

        # 5. Cooldown (per-user / per-channel) — in-memory через StateManager.
        cd_msg = await self._check_and_set_cooldown(streamer, message, tts)
        if cd_msg is not None:
            # per-user КД: отвечаем в чат. per-channel КД: cd_msg == "" (молча).
            if cd_msg:
                await self.send_response(chat=streamer, message=cd_msg)
            return HandlerResult.SKIPED

        # 6-8. Очистка текста, модерация финального текста, обрезка.
        text = await self._clean_moderate_truncate(streamer, message, tts)
        if text is None:
            return HandlerResult.SKIPED

        # 9. Озвучивать ник.
        if tts.read_username:
            text = f"{message.chatter_user_name} говорит {text}"

        # 10. Broadcast в SSE TTS.
        await ssem.broadcast(
            int(streamer.twitch_id),
            SSEChannel.TTS,
            json.dumps({"text": text, "model": tts.model}),
        )
        if self._statistics is not None:
            self._statistics.inc("tts_messages")

        # Не блокируем команды/другие handlers.
        return HandlerResult.HANDLED_AND_CONTINUE

    async def _check_and_set_cooldown(self, streamer: User, message: ChatMessageWebhookEventSchema, tts) -> str | None:
        """Проверить и проставить КД.

        :return: ``None`` — можно озвучивать. ``""`` — per-channel КД (молча skip).
            ``"текст"`` — per-user КД (текст — сообщение для чата).
        """
        per_user = tts.cooldown_per_user
        per_channel = tts.cooldown_per_channel
        if not per_user and not per_channel:
            return None

        now = time()

        if per_user:
            last_user = await self._state_manager.get_state(
                channel=streamer.login_name,
                user=message.chatter_user_id,
                command=_TTS_CD_COMMAND,
                param=SMParam.COOLDOWN,
            )
            if last_user and now - last_user < per_user:
                remaining = int(per_user - (now - last_user))
                logger.debug("TTS skip: per-user cooldown (%ds left)", remaining)
                return f"@{message.chatter_user_login}, подожди {remaining} сек, прежде чем отправлять ещё TTS"

        if per_channel:
            last_chan = await self._state_manager.get_state(
                channel=streamer.login_name,
                command=_TTS_CD_COMMAND,
                param=SMParam.COOLDOWN,
            )
            if last_chan and now - last_chan < per_channel:
                logger.debug("TTS skip: per-channel cooldown")
                return ""

        if per_user:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                user=message.chatter_user_id,
                command=_TTS_CD_COMMAND,
                param=SMParam.COOLDOWN,
                value=now,
            )
        if per_channel:
            await self._state_manager.set_state(
                channel=streamer.login_name,
                command=_TTS_CD_COMMAND,
                param=SMParam.COOLDOWN,
                value=now,
            )
        return None

    @property
    def _statistics(self):
        return self._container.statistics()

    async def _clean_moderate_truncate(
        self,
        streamer: User,
        message: ChatMessageWebhookEventSchema,
        tts,
    ) -> str | None:
        """Очистка текста → модерация финального текста → обрезка.

        :return: готовый текст для озвучки, либо None если сообщение
            пустое/заблокировано модерацией (в последнем случае сам отправляет
            чат-варн и инкремент статистики).
        """
        text = self._extract_text_fragments(message)
        text = clean_tts_text(text)
        if not text:
            return None

        moderation: ModerationService = self._container.moderation_service()
        result = moderation.validate(text)
        if result.is_banned:
            if self._statistics is not None:
                self._statistics.inc("tts_blocked", subtype="command")
            await self.send_response(chat=streamer, message="⚠️ TTS: сообщение заблокировано модерацией.")
            return None

        text = truncate_tts(text, tts.max_length)
        return text

    @staticmethod
    def _detect_trigger(message: ChatMessageWebhookEventSchema, text: str) -> TTSTrigger | None:
        """Определить триггер TTS по старшинству (command > streamer_tag > all_no_replies > all)."""
        if text.startswith(TTS_COMMAND_PREFIX):
            return TTSTrigger.COMMAND
        # Тег стримера: mention-фрагмент на login стримера.
        broadcaster_login = message.broadcaster_user_login.lower()
        for frag in message.message.fragments:
            if frag.mention and frag.mention.user_login.lower() == broadcaster_login:
                return TTSTrigger.STREAMER_TAG
        if message.reply is None:
            return TTSTrigger.ALL_NO_REPLIES
        return TTSTrigger.ALL

    @staticmethod
    def _extract_text_fragments(message: ChatMessageWebhookEventSchema) -> str:
        """Собрать текст только из text-фрагментов (без emote/mention)."""
        parts: list[str] = []
        for frag in message.message.fragments:
            if frag.type == "text":
                parts.append(frag.text)
        return " ".join(parts)
