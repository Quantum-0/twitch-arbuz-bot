"""API-роутер для Telegram-интеграции: получение/обновление настроек, генерация deep-link."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from config import settings
from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth
from schemas.telegram import (
    TelegramConnectSchema,
    TelegramSettingsSchema,
    TelegramSettingsUpdateSchema,
)
from utils.telegram import ensure_telegram_settings, get_telegram_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["Telegram"])


@router.get("/settings", response_model=TelegramSettingsSchema)
async def get_telegram_settings_endpoint(
    user: User = Security(user_auth),
) -> TelegramSettingsSchema:
    """Получить текущие настройки Telegram-интеграции.

    Если запись в БД ещё не создана — возвращаются дефолты (всё выключено, чаты не подключены).
    """
    tg = get_telegram_settings(user)
    is_connected = bool(tg.stream_chat_id or tg.clips_chat_id or tg.stickers_chat_id)
    return TelegramSettingsSchema(
        stream_chat_id=tg.stream_chat_id,
        clips_chat_id=tg.clips_chat_id,
        stickers_chat_id=tg.stickers_chat_id,
        is_connected=is_connected,
        stream_notification_enabled=tg.stream_notification_enabled,
        stream_offline_behavior=tg.stream_offline_behavior,
        clips_enabled=tg.clips_enabled,
        clips_mode=tg.clips_mode,
        stickers_enabled=tg.stickers_enabled,
        stickers_mode=tg.stickers_mode,
        twitch_to_tg_enabled=tg.twitch_to_tg_enabled,
        tg_to_twitch_enabled=tg.tg_to_twitch_enabled,
    )


@router.post("/settings")
async def update_telegram_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    data: TelegramSettingsUpdateSchema,
    user: User = Security(user_auth),
) -> JSONResponse:
    """Обновить настройки Telegram-интеграции (частично).

    Автоматически создаёт строку TelegramSettings, если её ещё нет (lazy creation).
    """
    tg = await ensure_telegram_settings(db, user)
    for field in data.model_fields_set:
        value = getattr(data, field)
        if value is not None:
            setattr(tg, field, value)
    await db.commit()
    return JSONResponse({"title": "Сохранено", "message": "Настройки Telegram обновлены."}, 200)


@router.post("/connect")
async def generate_connect_link(
    data: TelegramConnectSchema,
    user: User = Security(user_auth),  # noqa: B008
) -> JSONResponse:
    """Сгенерировать deep-link для подключения Telegram-чата.

    Возвращает URL вида ``https://t.me/<bot>?start=<jwt>`` — открывает
    приватный чат с ботом, бот GUID'ит пользователя к добавлению в канал/группу.
    JWT содержит ``user_id``, ``scope`` (stream/clips/stickers), ``chat_type``
    (channel/group), срок жизни 5 минут.
    """
    payload = {
        "user_id": user.id,
        "scope": data.scope,
        "chat_type": data.chat_type,
        "iat": datetime.now(tz=UTC),
        "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
    }
    token = jwt.encode(payload, settings.telegram_state_secret.get_secret_value(), algorithm="HS256")
    url = f"https://t.me/{settings.telegram_bot_username}?start={token}"
    return JSONResponse({"url": url}, 200)


@router.post("/disconnect")
async def disconnect_telegram(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User = Security(user_auth),
) -> JSONResponse:
    """Отключить Telegram-интеграцию: очищает все привязки чатов и сбрасывает настройки."""
    if user.telegram is not None:
        user.telegram.stream_chat_id = None
        user.telegram.stream_chat_type = None
        user.telegram.stream_connected_at = None
        user.telegram.clips_chat_id = None
        user.telegram.clips_chat_type = None
        user.telegram.clips_connected_at = None
        user.telegram.stickers_chat_id = None
        user.telegram.stickers_chat_type = None
        user.telegram.stickers_connected_at = None
        user.telegram.stream_notification_enabled = False
        user.telegram.clips_enabled = False
        user.telegram.stickers_enabled = False
        user.telegram.twitch_to_tg_enabled = False
        user.telegram.tg_to_twitch_enabled = False
        user.telegram.last_stream_message_id = None
        await db.commit()
    return JSONResponse({"title": "Готово", "message": "Telegram-интеграция отключена."}, 200)
