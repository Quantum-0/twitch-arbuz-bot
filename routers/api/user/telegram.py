"""API-роутер для Telegram-интеграции: получение/обновление настроек, генерация deep-link."""

import logging
from typing import Annotated

import httpx
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from config import settings
from container import Container
from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth
from schemas.telegram import (
    TelegramConnectSchema,
    TelegramSettingsSchema,
    TelegramSettingsUpdateSchema,
)
from services.mqtt import MQTTClient
from twitch.client.twitch import Twitch
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
        stream_chat_title=tg.stream_chat_title,
        clips_chat_id=tg.clips_chat_id,
        clips_chat_title=tg.clips_chat_title,
        stickers_chat_id=tg.stickers_chat_id,
        stickers_chat_title=tg.stickers_chat_title,
        is_connected=is_connected,
        stream_notification_enabled=tg.stream_notification_enabled,
        stream_offline_behavior=tg.stream_offline_behavior,
        stream_message_template=tg.stream_message_template,
        clips_enabled=tg.clips_enabled,
        clips_mode=tg.clips_mode,
        clips_delivery=tg.clips_delivery,
        stickers_enabled=tg.stickers_enabled,
        stickers_mode=tg.stickers_mode,
        twitch_to_tg_enabled=tg.twitch_to_tg_enabled,
        tg_to_twitch_enabled=tg.tg_to_twitch_enabled,
    )


@router.post("/settings")
@inject
async def update_telegram_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    data: TelegramSettingsUpdateSchema,
    user: User = Security(user_auth),
) -> JSONResponse:
    """Обновить настройки Telegram-интеграции (частично).

    Автоматически создаёт строку TelegramSettings, если её ещё нет (lazy creation).
    При включении ``stream_notification_enabled``:
    - Если stream-чат уже подключён — сразу создаёт EventSub-подписки. При ошибке
      создания откатывает тогл и возвращает ошибку (чтобы юзер видел реальное состояние).
    - Если stream-чат ещё не подключён — тогл сохраняется включённым; подписки будут
      созданы автоматически при подключении чата (см. ``handle_chat_connected``)
      или при следующем логине (см. ``login_callback_task``).
    При выключении — отписывается от EventSub.
    """
    tg = await ensure_telegram_settings(db, user)
    old_stream_enabled = tg.stream_notification_enabled
    for field in data.model_fields_set:
        value = getattr(data, field)
        if value is not None:
            setattr(tg, field, value)
    await db.commit()

    new_stream_enabled = tg.stream_notification_enabled
    if new_stream_enabled == old_stream_enabled:
        return JSONResponse({"title": "Сохранено", "message": "Настройки Telegram обновлены."}, 200)

    if new_stream_enabled:
        # Включили уведомления. Если чат уже подключён — подписываемся сразу.
        # Если чата нет — тогл остаётся, подписка произойдёт при подключении чата.
        if tg.stream_chat_id:
            try:
                await twitch.subscribe_stream_online(user)
                await twitch.subscribe_stream_offline(user)
                logger.info("stream.online/offline подписки созданы для user_id=%s", user.id)
            except Exception:
                logger.error("Ошибка создания stream.online/offline подписок", exc_info=True)
                # Откатываем тогл: юзер должен видеть, что уведомления не активировались.
                tg.stream_notification_enabled = False
                await db.commit()
                return JSONResponse(
                    {
                        "title": "Ошибка",
                        "message": "Не удалось включить уведомления о стриме. "
                        "Проверьте, что чат подключён, и попробуйте позже.",
                    },
                    502,
                )
    else:
        # Выключили уведомления — отписываемся.
        try:
            await twitch.unsubscribe_stream_online(user)
            await twitch.unsubscribe_stream_offline(user)
            logger.info("stream.online/offline подписки удалены для user_id=%s", user.id)
        except Exception:
            logger.error("Ошибка удаления stream.online/offline подписок", exc_info=True)

    return JSONResponse({"title": "Сохранено", "message": "Настройки Telegram обновлены."}, 200)


@router.post("/connect")
async def generate_connect_link(
    data: TelegramConnectSchema,
    user: User = Security(user_auth),  # noqa: B008
) -> JSONResponse:
    """Сгенерировать deep-link для подключения Telegram-чата.

    Вызывает TG-микросервис ``POST /api/connect`` для создания pending-подключения
    и получения ``short_id``. Возвращает URL ``https://t.me/<bot>?start=<short_id>``.
    ``short_id`` (8 hex chars) укладывается в лимит Telegram 64 байта для ``?start=``.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.telegram_service_url}/api/connect",
                headers={"X-Api-Key": settings.telegram_service_api_key},
                json={
                    "user_id": user.id,
                    "scope": data.scope,
                    "chat_type": data.chat_type,
                },
                timeout=10,
            )
    except httpx.ConnectError:
        logger.error("TG-сервис недоступен: %s", settings.telegram_service_url, exc_info=True)
        return JSONResponse({"title": "Ошибка", "message": "TG-сервис недоступен."}, 503)

    if not response.is_success:
        logger.error("TG-сервис вернул %s: %s", response.status_code, response.text)
        return JSONResponse({"title": "Ошибка", "message": "Не удалось создать подключение."}, 502)

    short_id = response.json().get("short_id")
    if not short_id:
        return JSONResponse({"title": "Ошибка", "message": "TG-сервис не вернул short_id."}, 502)

    url = f"https://t.me/{settings.telegram_bot_username}?start={short_id}"
    return JSONResponse({"url": url}, 200)


@router.post("/disconnect/{scope}")
@inject
async def disconnect_telegram_scope(
    scope: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    mqtt: Annotated[MQTTClient, Depends(Provide[Container.mqtt])],
    user: User = Security(user_auth),  # noqa: B008
) -> JSONResponse:
    """Отключить конкретный scope Telegram-интеграции.

    Очищает привязку чата для выбранного scope (stream/clips/stickers),
    отправляет боту команду покинуть чат (MQTT telegram/leave_chat),
    и для stream снимает EventSub-подписки.
    """
    valid_scopes = {"stream", "clips", "stickers"}
    if scope not in valid_scopes:
        return JSONResponse({"title": "Ошибка", "message": f"Неизвестный scope: {scope}"}, 400)

    tg = user.telegram
    scope_labels = {
        "stream": "Уведомления о стриме",
        "clips": "Клипы",
        "stickers": "AI-стикеры",
    }
    label = scope_labels[scope]

    if tg is not None:
        chat_id = None
        if scope == "stream":
            chat_id = tg.stream_chat_id
        elif scope == "clips":
            chat_id = tg.clips_chat_id
        elif scope == "stickers":
            chat_id = tg.stickers_chat_id

        if not chat_id:
            return JSONResponse(
                {"title": "Нечего отключать", "message": f"{label}: чат не подключён."},
                200,
            )

        if scope == "stream":
            tg.stream_chat_id = None
            tg.stream_chat_type = None
            tg.stream_chat_title = None
            tg.stream_connected_at = None
            tg.stream_notification_enabled = False
            tg.last_stream_message_id = None
            tg.twitch_to_tg_enabled = False
            tg.tg_to_twitch_enabled = False
        elif scope == "clips":
            tg.clips_chat_id = None
            tg.clips_chat_type = None
            tg.clips_chat_title = None
            tg.clips_connected_at = None
            tg.clips_enabled = False
        elif scope == "stickers":
            tg.stickers_chat_id = None
            tg.stickers_chat_type = None
            tg.stickers_chat_title = None
            tg.stickers_connected_at = None
            tg.stickers_enabled = False
        await db.commit()

        try:
            await mqtt.publish("telegram/leave_chat", {"chat_id": chat_id})
        except Exception:
            logger.error("Ошибка отправки leave_chat для scope=%s chat_id=%s", scope, chat_id, exc_info=True)

        if scope == "stream":
            try:
                await twitch.unsubscribe_stream_online(user)
                await twitch.unsubscribe_stream_offline(user)
            except Exception:
                logger.error("Ошибка отписки stream.online/offline при disconnect scope=stream", exc_info=True)

    return JSONResponse(
        {"title": "Готово", "message": f"{label} отключены. Бот покинет чат."},
        200,
    )
