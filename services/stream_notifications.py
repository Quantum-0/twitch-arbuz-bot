"""Telegram-уведомления о начале/окончании/перезапуске стрима.

Выделено из ``TwitchEventSubService`` (там остался EventSub-домен: награды,
мемкоины, стикеры, TTS, revocation). Спецификация поведения — ``docs/telegram.md``.

Модель (W = ``settings.stream_restart_window_minutes``, дефолт 15 мин):

- ``stream.offline`` → ничего не делает сразу: ставит APScheduler-джобу
  ``stream_offline_deferred:{user_id}`` на ``now + W`` (``replace_existing`` —
  серия падений перезапускает таймер).
- ``stream.online`` при живой джобе → быстрый перезапуск: джоба отменяется;
  ``silent``/``edit`` — тишина, ``notify`` — сообщение о рестарте не чаще
  одного за W (Redis-CD ``telegram:restart_notify_cd``).
- ``stream.online`` без джобы → новый стрим: обычный пост «начало стрима».
- Джоба (``process_deferred_stream_offline``) → проверка Get Streams: онлайн →
  тихо выйти (гонка offline/online); офлайн → подтверждённое окончание:
  ``delete``/``message``/``keep`` по ``stream_offline_behavior``.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from apscheduler.jobstores.base import JobLookupError
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from database.models import TelegramSettings, User
from schemas.twitch import StreamOfflineSchema, StreamOnlineSchema
from services.cache import Cache
from services.mqtt import MQTTClient
from twitch.client.twitch import Twitch

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_STREAM_ONLINE_REQUEST_PREFIX = "stream_online"
_STREAM_OFFLINE_REQUEST_PREFIX = "stream_offline"
_STREAM_OFFLINE_TEXT = "⚪️ Стрим завершён."
_STREAM_RESTART_TEXT = "🟠 Стрим упал, но был перезапущен."
# Redis-ключ CD сообщений о рестарте (режим notify): не чаще одного за окно.
_RESTART_NOTIFY_CD_REDIS_KEY = "telegram:restart_notify_cd"

# Префикс APScheduler-джобы подтверждения окончания стрима (docs/telegram.md):
# ставится в handle_stream_offline, снимается быстрым перезапуском или
# исполняется как подтверждённое окончание.
_DEFERRED_OFFLINE_JOB_PREFIX = "stream_offline_deferred"
# Ретрай подтверждения, если Get Streams недоступен в момент срабатывания джобы
# (live=None): до _DEFERRED_OFFLINE_MAX_ATTEMPTS проверок суммарно
# с интервалом _DEFERRED_OFFLINE_RETRY_MINUTES.
_DEFERRED_OFFLINE_MAX_ATTEMPTS = 3
_DEFERRED_OFFLINE_RETRY_MINUTES = 2


def _task_wrapper(func):
    async def wrapped(*args, **kwargs):
        asyncio.create_task(func(*args, **kwargs))

    return wrapped


def render_stream_restart_message(tg: TelegramSettings, streamer: str, link: str) -> str:
    """Сформировать текст уведомления о перезапуске стрима.

    Если задан ``stream_restart_message_template`` — использует его с
    плейсхолдерами ``{streamer}``, ``{link}``. При ошибке форматирования —
    fallback на дефолтный текст.
    """
    template = tg.stream_restart_message_template
    if not template or not template.strip():
        return _STREAM_RESTART_TEXT
    try:
        return template.format(streamer=streamer, link=link)
    except (KeyError, IndexError, ValueError):
        logger.warning(
            "Ошибка форматирования шаблона stream_restart_message_template, использую дефолт. template=%r",
            template,
        )
        return _STREAM_RESTART_TEXT


def render_stream_online_message(tg: TelegramSettings, streamer: str, title: str, category: str, link: str) -> str:
    """Сформировать текст уведомления о начале стрима.

    Если задан ``stream_message_template`` — использует его с плейсхолдерами
    ``{streamer}``, ``{title}``, ``{category}``, ``{link}``. При ошибке
    форматирования (неизвестный плейсхолдер) — fallback на дефолтный текст.
    """
    default = f"🔴 {streamer} начинает стрим!"
    template = tg.stream_message_template
    if not template or not template.strip():
        lines = [default]
        if title:
            lines.append(title)
        if category:
            lines.append(category)
        lines.append("")
        lines.append(link)
        return "\n".join(lines)
    try:
        return template.format(streamer=streamer, title=title, category=category, link=link)
    except (KeyError, IndexError, ValueError):
        logger.warning(
            "Ошибка форматирования шаблона stream_message_template, используем дефолт. template=%r",
            template,
        )
        lines = [default]
        if title:
            lines.append(title)
        if category:
            lines.append(category)
        lines.append("")
        lines.append(link)
        return "\n".join(lines)


def render_stream_offline_message(tg: TelegramSettings, streamer: str, link: str) -> str:
    """Сформировать текст уведомления об окончании стрима.

    Если задан ``stream_offline_message_template`` — использует его с
    плейсхолдерами ``{streamer}``, ``{link}`` (title/категорию при offline
    взять неоткуда — Get Streams уже пуст). При ошибке форматирования —
    fallback на дефолтный текст.
    """
    template = tg.stream_offline_message_template
    if not template or not template.strip():
        return _STREAM_OFFLINE_TEXT
    try:
        return template.format(streamer=streamer, link=link)
    except (KeyError, IndexError, ValueError):
        logger.warning(
            "Ошибка форматирования шаблона stream_offline_message_template, использую дефолт. template=%r",
            template,
        )
        return _STREAM_OFFLINE_TEXT


class StreamNotificationsService:
    """stream.online / stream.offline → Telegram-уведомления (docs/telegram.md).

    Вызывается из EventSub-роутера ``routers/api/twitch_eventsub.py``.
    """

    def __init__(
        self,
        twitch: Twitch,
        db_session_factory: Callable[[], AsyncSession],
        mqtt: MQTTClient | None = None,
        cache: Cache | None = None,
    ):
        self._twitch = twitch
        self._db_session_factory = db_session_factory
        self._mqtt = mqtt
        self._cache = cache

    async def _get_user(self, id_or_login: str | int) -> User:
        """Загрузить пользователя по twitch_id или login_name (с telegram-настройками)."""
        if not isinstance(id_or_login, int | str) or id_or_login == "":
            raise ValueError

        if isinstance(id_or_login, str) and id_or_login.isdigit():
            id_or_login = int(id_or_login)

        query: sa.Select[tuple[User]] = sa.Select(User).options(selectinload(User.telegram))
        if isinstance(id_or_login, str):
            query = query.where(User.login_name == id_or_login.lower())
        else:
            query = query.where(User.twitch_id == str(id_or_login))
        async with self._db_session_factory() as db:
            result = await db.execute(query)
            user = result.scalar_one_or_none()
            if user is None:
                raise Exception(f"User not found: `{id_or_login}`")
        return user

    @_task_wrapper
    @tracer.start_as_current_span("StreamNotifications: stream online")
    async def handle_stream_online(self, payload: StreamOnlineSchema | dict[str, Any]) -> None:
        """stream.online EventSub → отправить уведомление в Telegram-чат стрима.

        Cost = 0, scopes не требуются (app access token).
        request_id = ``stream_online:{user_id}`` — используется для корреляции
        результата (message_id) в ``handle_telegram_result``.

        Если offline-джоба ещё жива (стрим упал < W минут назад и его окончание
        не подтверждено) — это быстрый перезапуск: джоба отменяется, поведение
        по ``stream_restart_behavior`` (см. ``_handle_quick_restart``).
        Иначе — новый стрим, обычное уведомление.

        Заголовок и категория стрима подтягиваются отдельным запросом
        ``GET /helix/streams`` (app access token) — в самом событии stream.online
        этих полей нет (schema v1 содержит только broadcaster + started_at).
        """
        if isinstance(payload, dict):
            payload = StreamOnlineSchema.model_validate(payload, by_name=True)

        user = await self._get_user(payload.event.broadcaster_user_id)
        tg: TelegramSettings | None = user.telegram

        if tg is None or not tg.stream_notification_enabled or not tg.stream_chat_id:
            return

        channel_name = payload.event.broadcaster_user_name
        stream_url = f"https://twitch.tv/{payload.event.broadcaster_user_login}"

        # Быстрый перезапуск (< W после stream.offline): отменяем подтверждение окончания.
        if await self._cancel_deferred_stream_offline(user.id):
            await self._handle_quick_restart(user, tg, channel_name, stream_url)
            return

        # Новый стрим — обычное уведомление.
        # stream.online v1 не содержит title/категорию — подтягиваем через Get Streams.
        title, category = await self._fetch_stream_meta(user)

        message_text = render_stream_online_message(tg, channel_name, title, category, stream_url)

        # Старый offline-msg больше не актуален (на случай потерянного stream.offline).
        await self._set_stream_message_ids(user.id, online_message_id=tg.last_stream_message_id)

        request_id = f"{_STREAM_ONLINE_REQUEST_PREFIX}:{user.id}"
        await self._publish_send_message(
            tg.stream_chat_id,
            message_text,
            request_id,
            disable_web_page_preview=not tg.stream_link_preview_enabled,
        )

    async def _cancel_deferred_stream_offline(self, user_id: int) -> bool:
        """Отменить отложенную джобу подтверждения окончания стрима.

        Возвращает True, если джоба существовала — т.е. стрим упал < W минут
        назад и это быстрый перезапуск, а не новый стрим.
        """
        from container_runtime import get_container

        try:
            get_container().scheduler().remove_job(f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{user_id}")
            return True
        except JobLookupError:
            return False

    async def _handle_quick_restart(
        self,
        user: User,
        tg: TelegramSettings,
        channel_name: str,
        stream_url: str,
    ) -> None:
        """Обработать быстрый перезапуск стрима (< W после stream.offline).

        Окончание не подтверждено, offline-действия не применялись, пост о
        начале стрима остаётся на месте. Поведение по ``stream_restart_behavior``
        (docs/telegram.md):

        - ``silent``/``edit`` — тишина: ничего не удаляем и не редактируем,
          будто стрим не прерывался (``edit`` ≡ ``silent``, сохранён для
          совместимости).
        - ``notify`` — сразу отправить сообщение о перезапуске, но не чаще
          одного за окно: Redis-CD ``telegram:restart_notify_cd:{user_id}``
          с TTL = W (анти-спам на серию падений).
        """
        behavior = tg.stream_restart_behavior or "edit"
        chat_id = tg.stream_chat_id
        if not chat_id:  # проверено в handle_stream_online, защита для независимых вызовов
            return
        if behavior != "notify":
            logger.info(
                "Перезапуск стрима (< %d мин) скрыт для user_id=%s (behavior=%s)",
                settings.stream_restart_window_minutes,
                user.id,
                behavior,
            )
            return

        if self._cache is not None:
            cd_key = f"{_RESTART_NOTIFY_CD_REDIS_KEY}:{user.id}"
            if await self._cache.get_str(cd_key) is not None:
                logger.info("Перезапуск user_id=%s: CD активен, сообщение о рестарте не дублируем", user.id)
                return
            await self._cache.set_str(cd_key, "1", ttl=settings.stream_restart_window_minutes * 60)

        restart_text = render_stream_restart_message(tg, channel_name, stream_url)
        await self._publish_send_message(
            chat_id,
            restart_text,
            disable_web_page_preview=not tg.stream_link_preview_enabled,
        )
        logger.info("Перезапуск стрима: отправлено сообщение для user_id=%s", user.id)

    async def _is_stream_live(self, user: User) -> bool | None:
        """Проверить через Get Streams, что стрим сейчас идёт.

        Возвращает None при ошибке API — статус неизвестен (не подтверждён
        ни офлайн, ни онлайн): деструктивные действия не выполняем.
        """
        try:
            streams = await self._twitch.get_streams([user])
        except Exception:
            logger.warning("_is_stream_live: ошибка Get Streams для user_id=%s", user.id, exc_info=True)
            return None
        return streams.get(user) is not None

    async def _fetch_stream_meta(self, user: User) -> tuple[str, str]:
        """Получить title и категорию текущего стрима через ``GET /helix/streams``.

        Возвращает ``("", "")`` если стрим ещё не виден в Helix (бывает задержка
        между событием stream.online и появлением данных в Get Streams) или при
        ошибке API — уведомление всё равно отправляется, но без заголовка.
        """
        try:
            streams = await self._twitch.get_streams([user])
            stream = streams.get(user)
            if stream is None:
                return "", ""
            return stream.title or "", stream.game_name or ""
        except Exception:
            logger.warning("Не удалось получить title/категорию стрима для user_id=%s", user.id, exc_info=True)
            return "", ""

    @_task_wrapper
    @tracer.start_as_current_span("StreamNotifications: stream offline")
    async def handle_stream_offline(self, payload: StreamOfflineSchema | dict[str, Any]) -> None:
        """stream.offline EventSub → отложить подтверждение окончания на W минут.

        Ничего не отправляет и не удаляет сразу (docs/telegram.md): ставит
        APScheduler-джобу ``stream_offline_deferred:{user_id}`` в персистентный
        SQL jobstore (переживает рестарт приложения); ``replace_existing=True`` —
        серия падений просто перезапускает таймер. Джоба через W проверит
        Get Streams и только при подтверждённом офлайне выполнит
        ``stream_offline_behavior``:

        - ``delete``  → удалить сообщение о начале стрима (``last_stream_message_id``);
        - ``message`` → отправить сообщение об окончании (request_id =
          ``stream_offline:{user_id}``, message_id сохранится в
          ``last_stream_offline_message_id``);
        - ``keep``    → ничего.

        ``last_stream_message_id`` здесь НЕ чистится — он нужен джобе.
        """
        if isinstance(payload, dict):
            payload = StreamOfflineSchema.model_validate(payload, by_name=True)

        user = await self._get_user(payload.event.broadcaster_user_id)
        tg: TelegramSettings | None = user.telegram

        if tg is None or not tg.stream_notification_enabled or not tg.stream_chat_id:
            return

        from container_runtime import get_container

        run_date = datetime.now(UTC) + timedelta(minutes=settings.stream_restart_window_minutes)
        try:
            get_container().scheduler().add_job(
                process_deferred_stream_offline,
                trigger="date",
                run_date=run_date,
                id=f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{user.id}",
                replace_existing=True,
                misfire_grace_time=None,
                kwargs={"user_id": user.id},
            )
        except Exception:
            logger.error(
                "stream.offline user_id=%s: не удалось поставить stream_offline_deferred джобу",
                user.id,
                exc_info=True,
            )
            return
        logger.info(
            "stream.offline user_id=%s: подтверждение окончания отложено на %d мин",
            user.id,
            settings.stream_restart_window_minutes,
        )

    # ── MQTT-публикации в TG-микросервис ───────────────────────────────────

    async def _publish_send_message(
        self,
        chat_id: str,
        message_text: str,
        request_id: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> None:
        """Отправить текстовое сообщение в Telegram через MQTT."""
        if self._mqtt is None:
            logger.warning("MQTT не доступен, не могу отправить сообщение в Telegram")
            return

        payload: dict[str, Any] = {
            "request_id": request_id or str(uuid.uuid4()),
            "chat_id": chat_id,
            "message_text": message_text,
        }
        if disable_web_page_preview:
            payload["disable_web_page_preview"] = True
        await self._mqtt.publish("telegram/send_message", payload)

    async def _publish_edit_message(
        self,
        chat_id: str,
        message_id: str,
        message_text: str,
        request_id: str | None = None,
        fallback_message_text: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> None:
        """Отредактировать текст сообщения в Telegram через MQTT.

        Сейчас не используется (edit-режим рестарта упразднён, docs/telegram.md),
        сохранён под MQTT-контракт ``telegram/edit_message`` — пригодится для
        будущих фич (обновление постов без пересоздания).
        """
        if self._mqtt is None:
            logger.warning("MQTT не доступен, не могу отредактировать сообщение в Telegram")
            return

        payload: dict[str, Any] = {
            "request_id": request_id or str(uuid.uuid4()),
            "chat_id": chat_id,
            "message_id": message_id,
            "message_text": message_text,
        }
        if fallback_message_text is not None:
            payload["fallback_message_text"] = fallback_message_text
        if disable_web_page_preview:
            payload["disable_web_page_preview"] = True
        await self._mqtt.publish("telegram/edit_message", payload)

    async def _publish_delete_message(self, chat_id: str, message_id: str) -> None:
        """Удалить сообщение в Telegram через MQTT."""
        if self._mqtt is None:
            logger.warning("MQTT не доступен, не могу удалить сообщение в Telegram")
            return

        await self._mqtt.publish(
            "telegram/delete_message",
            {
                "request_id": str(uuid.uuid4()),
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )

    # ── message_id текущих постов в БД ─────────────────────────────────────

    async def _set_stream_message_ids(
        self,
        user_id: int,
        online_message_id: str | None = None,
        offline_message_id: str | None = None,
    ) -> None:
        """Обновить message_id последнего онлайн/оффлайн-сообщения в БД."""
        async with self._db_session_factory() as db:
            await db.execute(
                sa.update(TelegramSettings)
                .where(TelegramSettings.user_id == user_id)
                .values(
                    last_stream_message_id=online_message_id,
                    last_stream_offline_message_id=offline_message_id,
                )
            )
            await db.commit()

    async def _clear_online_message_id(self, user_id: int) -> None:
        """Обнулить только ``last_stream_message_id``.

        ``last_stream_offline_message_id`` не трогаем: его записывает колбэк
        ``handle_telegram_result`` по результату доставки «Завершён», и общая
        очистка могла бы затереть его (race publish → cleanup → callback).
        """
        async with self._db_session_factory() as db:
            await db.execute(
                sa.update(TelegramSettings)
                .where(TelegramSettings.user_id == user_id)
                .values(last_stream_message_id=None)
            )
            await db.commit()


async def process_deferred_stream_offline(user_id: int, attempt: int = 1) -> None:
    """APScheduler-джоба ``stream_offline_deferred:{user_id}`` — подтверждённое окончание стрима.

    Ставится в ``StreamNotificationsService.handle_stream_offline`` через W минут
    после stream.offline (docs/telegram.md). Проверяет через Get Streams, что
    стрим действительно офлайн, и только тогда выполняет
    ``stream_offline_behavior`` (delete → удалить пост о начале /
    message → «Завершён» / keep → ничего) и чистит ``last_stream_message_id``.

    Если Get Streams недоступен (статус неизвестен) — перезапланируется на
    ``_DEFERRED_OFFLINE_RETRY_MINUTES`` минут, всего не более
    ``_DEFERRED_OFFLINE_MAX_ATTEMPTS`` попыток.

    Top-level функция (не метод класса) — обязательное требование сериализации
    в персистентный SQL jobstore APScheduler.
    """
    from container_runtime import get_container

    container = get_container()
    service: StreamNotificationsService = container.stream_notifications()

    async with service._db_session_factory() as db:  # noqa: SLF001
        result = await db.execute(sa.select(User).options(selectinload(User.telegram)).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return
    tg = user.telegram
    if tg is None or not tg.stream_notification_enabled or not tg.stream_chat_id:
        return

    live = await service._is_stream_live(user)  # noqa: SLF001
    if live is None:
        if attempt >= _DEFERRED_OFFLINE_MAX_ATTEMPTS:
            logger.error(
                "stream_offline_deferred: user_id=%s — Get Streams недоступен (%d попыток), "
                "окончание не подтверждено, больше не повторяю",
                user_id,
                attempt,
            )
            return
        retry_at = datetime.now(UTC) + timedelta(minutes=_DEFERRED_OFFLINE_RETRY_MINUTES)
        try:
            container.scheduler().add_job(
                process_deferred_stream_offline,
                trigger="date",
                run_date=retry_at,
                id=f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{user_id}",
                replace_existing=True,
                misfire_grace_time=None,
                kwargs={"user_id": user_id, "attempt": attempt + 1},
            )
        except Exception:
            logger.error(
                "stream_offline_deferred: user_id=%s — не удалось перезапланировать попытку %d",
                user_id,
                attempt + 1,
                exc_info=True,
            )
            return
        logger.warning(
            "stream_offline_deferred: user_id=%s — Get Streams недоступен, окончание не подтверждено, "
            "повтор через %d мин (попытка %d/%d)",
            user_id,
            _DEFERRED_OFFLINE_RETRY_MINUTES,
            attempt + 1,
            _DEFERRED_OFFLINE_MAX_ATTEMPTS,
        )
        return
    if live:
        logger.info(
            "stream_offline_deferred: user_id=%s — стрим снова онлайн (гонка/отменённый рестарт), ничего не делаю",
            user_id,
        )
        return

    behavior = tg.stream_offline_behavior or "keep"
    if behavior == "delete" and tg.last_stream_message_id:
        await service._publish_delete_message(tg.stream_chat_id, tg.last_stream_message_id)  # noqa: SLF001
    elif behavior == "message":
        stream_url = f"https://twitch.tv/{user.login_name}"
        message_text = render_stream_offline_message(tg, user.login_name, stream_url)
        request_id = f"{_STREAM_OFFLINE_REQUEST_PREFIX}:{user_id}"
        await service._publish_send_message(  # noqa: SLF001
            tg.stream_chat_id,
            message_text,
            request_id,
            disable_web_page_preview=not tg.stream_link_preview_enabled,
        )
    # Чистим только online-id: offline-id заполняет колбэк доставки «Завершён».
    await service._clear_online_message_id(user_id)  # noqa: SLF001
    logger.info(
        "stream_offline_deferred: user_id=%s — окончание подтверждено, применён behavior=%s",
        user_id,
        behavior,
    )
