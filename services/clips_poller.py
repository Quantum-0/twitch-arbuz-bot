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
   - clips_delivery == "video" → GET /helix/clips/download → MQTT twibot/telegram/send_video.
6. Обновить last_clip_date = max(clip.created_at) или NOW() если клипов не было.
"""

from __future__ import annotations

import logging
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
_CLIPS_DOWNLOAD_URL = "https://api.twitch.tv/helix/clips/download"
_CLIPS_PAGE_SIZE = 100
_CLIPS_LOOKBACK_DAYS = 7


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
        else:
            started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
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

        if not clips:
            return

        if tg.clips_mode == "featured":
            clips = [c for c in clips if c.get("is_featured", False)]

        if not clips:
            return

        clips.sort(key=lambda c: c.get("created_at", ""))

        new_last_clip_date = started_at
        delivery = tg.clips_delivery or "link"

        for clip in clips:
            await self._send_clip(access_token, tg.clips_chat_id, clip, delivery)
            clip_created_at_str = clip.get("created_at", "")
            try:
                clip_dt = datetime.fromisoformat(clip_created_at_str.replace("Z", "+00:00")).replace(tzinfo=None)
                if clip_dt > new_last_clip_date:
                    new_last_clip_date = clip_dt
            except (ValueError, AttributeError):
                pass

        await self._update_last_clip_date(user.id, new_last_clip_date)

        logger.info(
            "Пуллинг клипов: отправлено %d клипов для user_id=%s (%s)",
            len(clips),
            user.id,
            user.login_name,
        )

    async def _send_clip(self, access_token: str, chat_id: str, clip: dict[str, Any], delivery: str) -> None:
        """Отправить один клип в Telegram: видео-файлом или ссылкой."""
        clip_id = clip.get("id", "")
        clip_url = clip.get("url") or self._build_clip_url(clip_id)
        clip_title = clip.get("title", "")
        creator = clip.get("creator_name", "")

        if delivery == "video":
            video_url = await self._fetch_clip_download_url(access_token, clip_id)
            if video_url:
                caption = f"🎬 {clip_title}\n👤 {creator}"
                await self._mqtt_publish_send_video(chat_id, video_url, caption)
                return
            logger.warning(
                "Пуллинг клипов: не удалось получить video URL для clip_id=%s, отправляю ссылку",
                clip_id,
            )

        caption_link = f"🎬 {clip_title}\n👤 {creator}\n🔗 {clip_url}"
        await self._mqtt_publish_send_message(chat_id, caption_link)

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

    async def _fetch_clip_download_url(self, access_token: str, clip_id: str) -> str | None:
        """Получить прямой URL для скачивания видео клипа через Twitch Get Clips Download API.

        Возвращает URL лучшего качества (первый в списке) или None при ошибке.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                _CLIPS_DOWNLOAD_URL,
                params={"id": clip_id},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Client-Id": settings.twitch_client_id,
                },
                timeout=15,
            )

        if not response.is_success:
            logger.warning(
                "Twitch Get Clips Download error: status=%s, body=%s",
                response.status_code,
                response.text[:300],
            )
            return None

        data = response.json().get("data", [])
        if not data:
            return None

        return data[0].get("url")

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
