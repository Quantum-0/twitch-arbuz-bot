"""Handler MQTT-сообщений от TG-микросервиса.

- ``telegram/chat_connected`` — сохранение привязки чата к пользователю.
- ``telegram/result/{request_id}`` — результат отправки сообщения; для stream.online
  уведомлений (request_id = ``stream_online:{user_id}``) сохраняет message_id в БД
  (``last_stream_message_id``) для последующего удаления при stream.offline.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from database.models import TelegramSettings
from schemas.telegram import SendResult

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


_STREAM_ONLINE_PREFIX = "stream_online"


async def handle_telegram_result(payload: dict[str, Any], db_session_factory) -> None:
    """Обработать результат отправки сообщения от TG-сервиса.

    Для stream.online уведомлений (request_id = ``stream_online:{user_id}``)
    сохраняет ``message_id`` в ``last_stream_message_id`` для последующего
    удаления при stream.offline.
    """
    try:
        result = SendResult(**payload)
    except Exception:
        logger.warning("telegram/result: невалидный payload: %s", payload)
        return

    if not result.request_id.startswith(_STREAM_ONLINE_PREFIX):
        return

    if not result.success or not result.message_id:
        logger.warning(
            "telegram/result: stream.online отправка не удалась: request_id=%s error=%s",
            result.request_id,
            result.error,
        )
        return

    # Извлекаем user_id из request_id = "stream_online:{user_id}"
    parts = result.request_id.split(":", 1)
    if len(parts) != 2:
        return
    try:
        user_id = int(parts[1])
    except ValueError:
        return

    async with db_session_factory() as db:
        await db.execute(
            sa.update(TelegramSettings)
            .where(TelegramSettings.user_id == user_id)
            .values(last_stream_message_id=result.message_id)
        )
        await db.commit()

    logger.info("telegram/result: last_stream_message_id обновлён для user_id=%s", user_id)
