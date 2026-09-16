"""Сервис пуллинга клипов Twitch и отправки их в Telegram.

APScheduler-джоба: раз в 5 минут проверяет новых клипов у всех пользователей,
у которых ``clips_enabled=True`` и ``clips_chat_id`` задан.

Алгоритм:
1. SELECT пользователей с clips_enabled + clips_chat_id + access_token.
2. Для каждого: получить валидный Twitch user token (через TwitchTokenService).
3. GET https://api.twitch.tv/helix/clips?broadcaster_id=<id>&started_at=<iso>&first=100
4. Фильтр по clips_mode: all → все, featured → только is_featured=True.
5. Для каждого нового клипа:
   - clips_delivery == "link" → MQTT twibot/telegram/send_message с ссылкой.
   - clips_delivery == "video" → GET /helix/clips/downloads → MQTT twibot/telegram/send_video.
     Fallback: thumbnail_url → MP4 URL (без доп. API-вызова).
     Fallback: ссылка на клип.
6. Обновить last_clip_date = max(clip.created_at) или NOW() если клипов не было.

thumbnail_url workaround — 0 доп. API-вызовов (URL уже в ответе Get Clips).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import joinedload

from config import settings
from database.models import TelegramSettings, User
from services.mqtt import MQTTClient
from services.twitch_token_service import TwitchTokenExpiredError, TwitchTokenService

logger = logging.getLogger(__name__)

_CLIPS_URL = "https://api.twitch.tv/helix/clips"
_CLIPS_DOWNLOAD_URL = "https://api.twitch.tv/helix/clips/downloads"
_CLIPS_PAGE_SIZE = 100
_CLIPS_LOOKBACK_DAYS = 7

# Паттерн для извлечения MP4 URL из thumbnail_url Twitch клипа.
# thumbnail_url: https://clips-media-assets2.twitch.tv/<ID>-offset-NNN-preview-%{width}x%{height}.jpg
# MP4 URL:       https://clips-media-assets2.twitch.tv/<ID>-offset-NNN.mp4
_THUMBNAIL_PREVIEW_RE = re.compile(r"-preview-(?:%\{width\}x%\{height\}|\d+x\d+)\.jpg$")


class ClipsPollerService:
    """Периодически проверяет новые клипы Twitch и отправляет их в Telegram через MQTT."""

    def __init__(
        self,
        db_session_factory: Any,
        twitch_token_service: TwitchTokenService,
        mqtt: MQTTClient,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._token_service = twitch_token_service
        self._mqtt = mqtt

    async def run_periodic_poll(self) -> None:
        """APScheduler-джоба: опросить клипы для всех подходящих пользователей."""
        logger.debug("Запуск пуллинга клипов")

        async with self._db_session_factory() as db:
            q = (
                sa.select(User)
                .options(joinedload(User.telegram))
                .join(TelegramSettings, TelegramSettings.user_id == User.id)
                .where(
                    TelegramSettings.clips_enabled.is_(True),
                    TelegramSettings.clips_chat_id.is_not(None),
                    User._access_token.is_not(None),
                )
            )
            users: list[User] = (await db.execute(q)).scalars().all()

        if not users:
            return

        logger.info("Пуллинг клипов: проверяем %d пользователей", len(users))

        for user in users:
            try:
                await self._poll_user_clips(user)
            except TwitchTokenExpiredError:
                logger.warning(
                    "Пуллинг клипов: токен истёк для user_id=%s (%s), пропускаем",
                    user.id,
                    user.login_name,
                )
            except Exception:
                logger.error(
                    "Пуллинг клипов: ошибка для user_id=%s (%s)",
                    user.id,
                    user.login_name,
                    exc_info=True,
                )

    async def _poll_user_clips(self, user: User) -> None:
        """Опросить клипы одного пользователя."""
        tg = user.telegram
        if tg is None or not tg.clips_chat_id:
            return

        access_token = await self._token_service.get_valid_access_token(user)

        if tg.last_clip_date is not None:
            started_at = tg.last_clip_date
            last_clip_date = tg.last_clip_date
        else:
            started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
            last_clip_date = None
            logger.info(
                "Пуллинг клипов: первый запуск для user_id=%s (%s), started_at=NOW",
                user.id,
                user.login_name,
            )

        min_started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=_CLIPS_LOOKBACK_DAYS)
        if started_at < min_started_at:
            started_at = min_started_at

        clips = await self._fetch_clips(
            access_token=access_token,
            broadcaster_id=user.twitch_id,
            started_at=started_at,
        )

        clips = self._filter_clips(clips, last_clip_date, tg.clips_mode)
        if not clips:
            return

        new_last_clip_date = last_clip_date or started_at
        delivery = tg.clips_delivery or "link"

        for clip in clips:
            await self._send_clip(access_token, user.twitch_id, tg.clips_chat_id, clip, delivery)
            clip_dt = self._parse_clip_created_at(clip.get("created_at", ""))
            if clip_dt > new_last_clip_date:
                new_last_clip_date = clip_dt

        await self._update_last_clip_date(user.id, new_last_clip_date)

        logger.info(
            "Пуллинг клипов: отправлено %d клипов для user_id=%s (%s)",
            len(clips),
            user.id,
            user.login_name,
        )

    async def _send_clip(
        self,
        access_token: str,
        broadcaster_id: str,
        chat_id: str,
        clip: dict[str, Any],
        delivery: str,
    ) -> None:
        """Отправить один клип в Telegram: видео-файлом или ссылкой.

        При ``delivery == "video"`` пытается получить прямой MP4 URL тремя способами
        (по порядку):
        1. Get Clips Download API (официальный, требует scope clips:edit).
        2. thumbnail_url workaround (0 доп. API-вызовов, без scope).
        3. Fallback на ссылку.
        """
        clip_id = clip.get("id", "")
        clip_url = clip.get("url") or self._build_clip_url(clip_id)
        clip_title = clip.get("title", "")
        creator = clip.get("creator_name", "")
        thumbnail_url = clip.get("thumbnail_url", "")

        if delivery == "video":
            # Способ 1: Get Clips Download API (официальный).
            video_url = await self._fetch_clip_download_url(access_token, broadcaster_id, clip_id)

            # Способ 2: thumbnail_url workaround (без доп. API-вызова).
            if not video_url and thumbnail_url:
                video_url = self._get_mp4_from_thumbnail_url(thumbnail_url)
                if video_url:
                    logger.info("Пуллинг клипов: video URL из thumbnail_url для clip_id=%s", clip_id)

            if video_url:
                caption = f"🎬 {clip_title}\n👤 {creator}"
                await self._mqtt_publish_send_video(chat_id, video_url, caption)
                return

            logger.warning(
                "Пуллинг клипов: не удалось получить video URL для clip_id=%s, отправляю ссылкой",
                clip_id,
            )

        caption_link = f"🎬 {clip_title}\n👤 {creator}\n🔗 {clip_url}"
        await self._mqtt_publish_send_message(chat_id, caption_link)

    def _filter_clips(
        self,
        clips: list[dict[str, Any]],
        last_clip_date: datetime | None,
        clips_mode: str,
    ) -> list[dict[str, Any]]:
        """Отфильтровать дубликаты (created_at <= last_clip_date) и применить clips_mode."""
        if last_clip_date is not None:
            clips = [c for c in clips if self._parse_clip_created_at(c.get("created_at", "")) > last_clip_date]
        if clips_mode == "featured":
            clips = [c for c in clips if c.get("is_featured", False)]
        clips.sort(key=lambda c: c.get("created_at", ""))
        return clips

    async def _fetch_clips(
        self,
        access_token: str,
        broadcaster_id: str,
        started_at: datetime,
    ) -> list[dict[str, Any]]:
        """Получить клипы через Twitch Helix API (с пагинацией)."""
        started_at_iso = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        all_clips: list[dict[str, Any]] = []
        cursor: str | None = None
        pages = 0

        async with httpx.AsyncClient() as client:
            while pages < 10:
                params: dict[str, str] = {
                    "broadcaster_id": broadcaster_id,
                    "started_at": started_at_iso,
                    "first": str(_CLIPS_PAGE_SIZE),
                }
                if cursor:
                    params["after"] = cursor

                response = await client.get(
                    _CLIPS_URL,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Client-Id": settings.twitch_client_id,
                    },
                    timeout=15,
                )

                if not response.is_success:
                    logger.warning(
                        "Twitch Get Clips error: status=%s, body=%s",
                        response.status_code,
                        response.text[:300],
                    )
                    return all_clips

                data = response.json()
                clips_data = data.get("data", [])
                all_clips.extend(clips_data)

                cursor = data.get("pagination", {}).get("cursor")
                if not cursor or len(clips_data) < _CLIPS_PAGE_SIZE:
                    break

                pages += 1

        return all_clips

    async def _fetch_clip_download_url(self, access_token: str, broadcaster_id: str, clip_id: str) -> str | None:
        """Получить прямой URL для скачивания видео клипа через Twitch Get Clips Download API.

        Endpoint: GET https://api.twitch.tv/helix/clips/downloads
        Требует scope: clips:edit (или channel:manage:clips / editor:manage:clips).
        Cost: 0 (бесплатно, rate-limited 100 req/min).

        Возвращает landscape_download_url (горизонтальный MP4) или None при ошибке.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                _CLIPS_DOWNLOAD_URL,
                params={
                    "editor_id": broadcaster_id,
                    "broadcaster_id": broadcaster_id,
                    "clip_id": clip_id,
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Client-Id": settings.twitch_client_id,
                },
                timeout=15,
            )

        if not response.is_success:
            logger.warning(
                "Twitch Get Clips Download error: status=%s, body=%s, clip_id=%s",
                response.status_code,
                response.text[:500],
                clip_id,
            )
            return None

        data = response.json().get("data", [])
        if not data:
            logger.warning("Twitch Get Clips Download: пустой data для clip_id=%s", clip_id)
            return None

        # API возвращает landscape_download_url и portrait_download_url.
        video_url = data[0].get("landscape_download_url")
        if not video_url:
            logger.warning("Twitch Get Clips Download: landscape_download_url=null для clip_id=%s", clip_id)
            return None

        logger.info("Пуллинг клипов: video URL из Get Clips Download API для clip_id=%s", clip_id)
        return video_url

    @staticmethod
    def _get_mp4_from_thumbnail_url(thumbnail_url: str) -> str | None:
        """Получить прямой MP4 URL из thumbnail_url клипа (workaround, 0 доп. API-вызовов).

        thumbnail_url Twitch содержит шаблон вида:
          https://clips-media-assets2.twitch.tv/<ID>-offset-NNN-preview-%{width}x%{height}.jpg
        MP4 URL получается заменой ``-preview-...jpg`` на ``.mp4``:
          https://clips-media-assets2.twitch.tv/<ID>-offset-NNN.mp4

        Возвращает None если URL не matches паттерн.
        """
        if not thumbnail_url:
            return None
        mp4_url = _THUMBNAIL_PREVIEW_RE.sub(".mp4", thumbnail_url)
        if mp4_url == thumbnail_url:
            # Паттерн не совпал — Twitch мог изменить формат CDN URL.
            logger.debug("thumbnail_url workaround: паттерн не совпал: %s", thumbnail_url[:200])
            return None
        return mp4_url

    async def _mqtt_publish_send_message(self, chat_id: str, message_text: str) -> None:
        """Отправить текстовое сообщение в Telegram через MQTT."""
        import uuid

        await self._mqtt.publish(
            "telegram/send_message",
            {
                "request_id": str(uuid.uuid4()),
                "chat_id": chat_id,
                "message_text": message_text,
            },
        )

    async def _mqtt_publish_send_video(self, chat_id: str, video_url: str, caption: str) -> None:
        """Отправить видео клипа в Telegram через MQTT."""
        import uuid

        await self._mqtt.publish(
            "telegram/send_video",
            {
                "request_id": str(uuid.uuid4()),
                "chat_id": chat_id,
                "video_url": video_url,
                "caption": caption,
            },
        )

    async def _update_last_clip_date(self, user_id: int, last_clip_date: datetime) -> None:
        """Обновить last_clip_date в БД."""
        async with self._db_session_factory() as db:
            await db.execute(
                sa.update(TelegramSettings)
                .where(TelegramSettings.user_id == user_id)
                .values(last_clip_date=last_clip_date)
            )
            await db.commit()

    @staticmethod
    def _build_clip_url(clip_id: str) -> str:
        """Построить URL клипа из ID (fallback если url не пришёл)."""
        return f"https://clips.twitch.tv/{clip_id}"

    @staticmethod
    def _parse_clip_created_at(created_at_str: str) -> datetime:
        """Парсинг ISO 8601 из Twitch API (напр. '2026-09-16T02:45:30Z') → naive UTC datetime."""
        try:
            return datetime.fromisoformat(created_at_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, AttributeError):
            return datetime.now(UTC).replace(tzinfo=None)
