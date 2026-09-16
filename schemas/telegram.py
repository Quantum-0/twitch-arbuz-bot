"""Pydantic-схемы для Telegram-интеграции (API + MQTT payloads).

Переиспользуются как body для HTTP API ручек TG-микросервиса (debug),
так и для парсинга MQTT payloads в основном сервисе и TG-сервисе.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── Настройки интеграции (основной сервис → фронтенд) ─────────────────────


class TelegramSettingsSchema(BaseModel):
    """Текущие настройки Telegram-интеграции для панели управления."""

    stream_chat_id: str | None = None
    clips_chat_id: str | None = None
    stickers_chat_id: str | None = None
    is_connected: bool = False

    stream_notification_enabled: bool = False
    stream_offline_behavior: Literal["delete", "message", "keep"] = "keep"

    clips_enabled: bool = False
    clips_mode: Literal["all", "featured"] = "all"
    clips_delivery: Literal["video", "link"] = "link"

    stickers_enabled: bool = False
    stickers_mode: Literal["photo", "document"] = "photo"

    twitch_to_tg_enabled: bool = False
    tg_to_twitch_enabled: bool = False


class TelegramSettingsUpdateSchema(BaseModel):
    """Частичное обновление настроек Telegram-интеграции."""

    stream_notification_enabled: bool | None = None
    stream_offline_behavior: Literal["delete", "message", "keep"] | None = None
    clips_enabled: bool | None = None
    clips_mode: Literal["all", "featured"] | None = None
    clips_delivery: Literal["video", "link"] | None = None
    stickers_enabled: bool | None = None
    stickers_mode: Literal["photo", "document"] | None = None
    twitch_to_tg_enabled: bool | None = None
    tg_to_twitch_enabled: bool | None = None


class TelegramConnectSchema(BaseModel):
    """Запрос на генерацию deep-link для подключения чата."""

    scope: Literal["stream", "clips", "stickers"]
    chat_type: Literal["channel", "group"]


# ── Контракты MQTT-сообщений (основной → TG-сервис) ────────────────────────
# Переиспользуются для API ручек (debug) и MQTT payload парсинга.


class SendMessageRequest(BaseModel):
    """Отправить текстовое сообщение в Telegram-чат."""

    request_id: str = Field(..., description="UUID для корреляции response")
    chat_id: str
    message_text: str


class SendPhotoRequest(BaseModel):
    """Отправить фото в Telegram-чат (TG-сервис скачивает по URL)."""

    request_id: str
    chat_id: str
    photo_url: str
    caption: str | None = None


class SendVideoRequest(BaseModel):
    """Отправить видео в Telegram-чат (TG-сервис скачивает по URL)."""

    request_id: str
    chat_id: str
    video_url: str
    caption: str | None = None


class SendDocumentRequest(BaseModel):
    """Отправить документ в Telegram-чат (TG-сервис скачивает по URL)."""

    request_id: str
    chat_id: str
    document_url: str
    caption: str | None = None


class DeleteMessageRequest(BaseModel):
    """Удалить сообщение в Telegram-чате по message_id."""

    request_id: str
    chat_id: str
    message_id: str


# ── Ответ TG-сервиса (через MQTT twibot/telegram/result/{request_id}) ──────


class SendResult(BaseModel):
    """Результат отправки сообщения/файла в Telegram."""

    request_id: str
    success: bool
    message_id: str | None = None
    error: str | None = None
