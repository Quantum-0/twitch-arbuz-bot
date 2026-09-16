"""Handler MQTT-сообщения telegram/chat_connected от TG-микросервиса.

Когда пользователь добавляет бота в Telegram-чат, TG-сервис публикует
twibot/telegram/chat_connected с {user_id, scope, chat_id, chat_type, chat_title}.
Этот handler сохраняет привязку в TelegramSettings.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from database.models import TelegramSettings

logger = logging.getLogger(__name__)


async def handle_chat_connected(payload: dict[str, Any], db_session_factory) -> None:
    """Сохранить привязку Telegram-чата к пользователю.

    payload: {user_id, scope, chat_id, chat_type, chat_title}
    """
    user_id = payload.get("user_id")
    scope = payload.get("scope")
    chat_id = str(payload.get("chat_id", ""))
    chat_type = payload.get("chat_type", "")
    chat_title = payload.get("chat_title", "")

    if not user_id or not scope or not chat_id:
        logger.warning("chat_connected: неполный payload: %s", payload)
        return

    logger.info(
        "chat_connected: user_id=%s scope=%s chat_id=%s chat_type=%s title=%s",
        user_id,
        scope,
        chat_id,
        chat_type,
        chat_title,
    )

    now = datetime.now(UTC).replace(tzinfo=None)

    async with db_session_factory() as db:
        # Гарантируем, что строка TelegramSettings существует
        existing = await db.execute(sa.select(TelegramSettings).where(TelegramSettings.user_id == user_id))
        tg = existing.scalar_one_or_none()

        if tg is None:
            tg = TelegramSettings(user_id=user_id)
            db.add(tg)
            await db.flush()

        if scope == "stream":
            tg.stream_chat_id = chat_id
            tg.stream_chat_type = chat_type
            tg.stream_connected_at = now
        elif scope == "clips":
            tg.clips_chat_id = chat_id
            tg.clips_chat_type = chat_type
            tg.clips_connected_at = now
        elif scope == "stickers":
            tg.stickers_chat_id = chat_id
            tg.stickers_chat_type = chat_type
            tg.stickers_connected_at = now
        else:
            logger.warning("chat_connected: неизвестный scope=%s", scope)
            return

        await db.commit()
