"""Хелперы для Telegram-настроек: lazy-доступ к TelegramSettings с дефолтами и lazy-создание строки."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TelegramSettings, User

# Дефолтные значения (когда строки TelegramSettings ещё нет).
DEFAULT_TELEGRAM = {
    "stream_chat_id": None,
    "stream_chat_type": None,
    "stream_chat_title": None,
    "clips_chat_id": None,
    "clips_chat_type": None,
    "clips_chat_title": None,
    "stickers_chat_id": None,
    "stickers_chat_type": None,
    "stickers_chat_title": None,
    "stream_notification_enabled": False,
    "stream_offline_behavior": "keep",
    "stream_message_template": None,
    "clips_enabled": False,
    "clips_mode": "all",
    "stickers_enabled": False,
    "stickers_mode": "photo",
    "twitch_to_tg_enabled": False,
    "tg_to_twitch_enabled": False,
}


def get_telegram_settings(user: User) -> TelegramSettings:
    """Вернуть TelegramSettings пользователя. Если строки ещё нет — вернуть
    transient-объект, заполненный дефолтами (без записи в БД)."""
    if user.telegram is not None:
        return user.telegram
    return TelegramSettings(
        user_id=user.id,  # type: ignore[arg-type]
        stream_notification_enabled=DEFAULT_TELEGRAM["stream_notification_enabled"],
        stream_offline_behavior=DEFAULT_TELEGRAM["stream_offline_behavior"],
        clips_enabled=DEFAULT_TELEGRAM["clips_enabled"],
        clips_mode=DEFAULT_TELEGRAM["clips_mode"],
        stickers_enabled=DEFAULT_TELEGRAM["stickers_enabled"],
        stickers_mode=DEFAULT_TELEGRAM["stickers_mode"],
        twitch_to_tg_enabled=DEFAULT_TELEGRAM["twitch_to_tg_enabled"],
        tg_to_twitch_enabled=DEFAULT_TELEGRAM["tg_to_twitch_enabled"],
    )


async def ensure_telegram_settings(db: AsyncSession, user: User) -> TelegramSettings:
    """Гарантировать наличие строки TelegramSettings в БД. Если её нет — создать и
    зафиксировать. Возвращает persisted-объект (привязанный к user.telegram)."""
    if user.telegram is not None:
        return user.telegram
    stmt = (
        pg_insert(TelegramSettings)
        .values(
            user_id=user.id,
            stream_notification_enabled=DEFAULT_TELEGRAM["stream_notification_enabled"],
            stream_offline_behavior=DEFAULT_TELEGRAM["stream_offline_behavior"],
            clips_enabled=DEFAULT_TELEGRAM["clips_enabled"],
            clips_mode=DEFAULT_TELEGRAM["clips_mode"],
            stickers_enabled=DEFAULT_TELEGRAM["stickers_enabled"],
            stickers_mode=DEFAULT_TELEGRAM["stickers_mode"],
        )
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    await db.execute(stmt)
    result = await db.execute(sa.select(TelegramSettings).where(TelegramSettings.user_id == user.id))
    obj = result.scalar_one()
    user.telegram = obj
    return obj
