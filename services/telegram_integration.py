"""Handler MQTT-сообщений от TG-микросервиса.

- ``telegram/chat_connected`` — сохранение привязки чата к пользователю.
  При подключении stream-чата, если у юзера уже включены уведомления о стриме
  (``stream_notification_enabled=True``), автоматически создаёт EventSub-подписки
  stream.online / stream.offline — чтобы не ждать следующего логина.
- ``telegram/result/{request_id}`` — результат отправки сообщения; для stream.online
  уведомлений (request_id = ``stream_online:{user_id}``) сохраняет message_id в БД
  (``last_stream_message_id``) для последующего удаления при stream.offline.
- ``reconcile_stream_subscriptions`` — периодическая сверка (APScheduler):
  если у юзера включены уведомления, но EventSub-подписок нет, пытается
  пересоздать; при неудаче — снимает галочку ``stream_notification_enabled``.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from database.models import TelegramSettings, User
from schemas.telegram import SendResult

logger = logging.getLogger(__name__)

# Маппинг scope → (chat_id, chat_type, chat_title, connected_at) для очистки привязки.
_SCOPE_COLUMNS = {
    "stream": ("stream_chat_id", "stream_chat_type", "stream_chat_title", "stream_connected_at"),
    "clips": ("clips_chat_id", "clips_chat_type", "clips_chat_title", "clips_connected_at"),
    "stickers": ("stickers_chat_id", "stickers_chat_type", "stickers_chat_title", "stickers_connected_at"),
}


async def handle_chat_connected(payload: dict[str, Any], db_session_factory) -> None:
    """Сохранить привязку Telegram-чата к пользователю.

    payload: {user_id, scope, chat_id, chat_type, chat_title}

    Для scope == "stream": если у юзера уже включены уведомления о стриме
    (``stream_notification_enabled=True``), сразу создаёт EventSub-подписки
    stream.online / stream.offline — иначе юзер получил бы уведомления только
    после следующего логина на сайте (см. ``login_callback_task``).
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
            tg.stream_chat_title = chat_title
            tg.stream_connected_at = now
        elif scope == "clips":
            tg.clips_chat_id = chat_id
            tg.clips_chat_type = chat_type
            tg.clips_chat_title = chat_title
            tg.clips_connected_at = now
        elif scope == "stickers":
            tg.stickers_chat_id = chat_id
            tg.stickers_chat_type = chat_type
            tg.stickers_chat_title = chat_title
            tg.stickers_connected_at = now
        else:
            logger.warning("chat_connected: неизвестный scope=%s", scope)
            return

        # Для stream: подписываемся на EventSub сразу, если уведомления уже включены.
        # Делаем это в той же транзакции после коммита настроек — чтобы Twitch-вызовы
        # не блокировали сессию БД. Поэтому запоминаем флаг и user_id, коммитим, затем
        # подписываемся вне сессии.
        should_subscribe_stream = scope == "stream" and tg.stream_notification_enabled and bool(tg.stream_chat_id)
        stream_user_id = tg.user_id

        await db.commit()

    if should_subscribe_stream:
        await _ensure_stream_subscriptions(stream_user_id, db_session_factory)


async def _ensure_stream_subscriptions(user_id: int, db_session_factory) -> bool:
    """Создать stream.online / stream.offline EventSub-подписки для пользователя.

    Возвращает True при успехе, False при ошибке. Логирует ошибки, но не бросает.
    Используется:
    - из ``handle_chat_connected`` (при подключении чата, если тогл уже включён);
    - из ``update_telegram_settings`` (при включении тогла, если чат уже подключён);
    - из ``reconcile_stream_subscriptions`` (периодическая сверка).
    """
    from container_runtime import get_container

    container = get_container()
    twitch = container.twitch()

    async with db_session_factory() as db:
        user = (await db.execute(sa.select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is None:
            logger.warning("_ensure_stream_subscriptions: user_id=%s не найден", user_id)
            return False

    try:
        await twitch.subscribe_stream_online(user)
        await twitch.subscribe_stream_offline(user)
        logger.info("stream.online/offline подписки созданы для user_id=%s", user_id)
        return True
    except Exception:
        logger.error(
            "Не удалось создать stream.online/offline подписки для user_id=%s",
            user_id,
            exc_info=True,
        )
        return False


async def _disable_stream_notification(user_id: int, db_session_factory) -> None:
    """Снять галочку ``stream_notification_enabled`` в БД (EventSub слетела/не создаётся)."""
    async with db_session_factory() as db:
        await db.execute(
            sa.update(TelegramSettings)
            .where(TelegramSettings.user_id == user_id)
            .values(stream_notification_enabled=False)
        )
        await db.commit()
    logger.warning(
        "stream_notification_enabled снят для user_id=%s (EventSub недоступна)",
        user_id,
    )


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


async def _reconcile_user_subs(
    user: User,
    has_online: bool,
    has_offline: bool,
    twitch,
    db_session_factory,
) -> bool:
    """Создать недостающие EventSub-подписки для одного юзера. Возвращает True если был сбой."""
    failed = False
    if not has_online:
        try:
            await twitch.subscribe_stream_online(user)
            logger.info("reconcile: stream.online создан для user_id=%s", user.id)
        except Exception:
            logger.error("reconcile: не удалось создать stream.online для user_id=%s", user.id, exc_info=True)
            failed = True
    if not has_offline and not failed:
        try:
            await twitch.subscribe_stream_offline(user)
            logger.info("reconcile: stream.offline создан для user_id=%s", user.id)
        except Exception:
            logger.error("reconcile: не удалось создать stream.offline для user_id=%s", user.id, exc_info=True)
            failed = True
    if failed:
        await _disable_stream_notification(user.id, db_session_factory)
    return failed


async def reconcile_stream_subscriptions(db_session_factory) -> None:
    """Периодическая сверка (APScheduler): для каждого юзера с включёнными
    уведомлениями о стриме проверяет наличие EventSub-подписок stream.online /
    stream.offline.

    - Если подписок нет — пытается пересоздать.
    - Если пересоздание не удалось — снимает галочку ``stream_notification_enabled``
      (чтобы юзер видел в панели, что уведомления фактически не работают, и мог
      включить их заново вручную).

    Один вызов ``get_subscriptions()`` отдаёт ВСЕ подписки приложения —
    фильтрация по ``broadcaster_user_id`` делается в памяти.
    """
    from container_runtime import get_container

    container = get_container()
    twitch = container.twitch()

    # Загружаем всех юзеров с включёнными stream-уведомлениями и подключённым чатом.
    async with db_session_factory() as db:
        users = (
            (
                await db.execute(
                    sa.select(User)
                    .join(TelegramSettings, TelegramSettings.user_id == User.id)
                    .where(
                        TelegramSettings.stream_notification_enabled.is_(True),
                        TelegramSettings.stream_chat_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    if not users:
        return

    # Сопоставление: twitch_id → user для быстрой фильтрации подписок.
    twitch_id_to_user = {u.twitch_id: u for u in users}

    # Получаем все подписки приложения и фильтруем по нашим юзерам.
    try:
        all_subs = await twitch.get_subscriptions()
    except Exception:
        logger.error("reconcile_stream_subscriptions: не удалось получить список подписок", exc_info=True)
        return

    online_present: set[str] = set()
    offline_present: set[str] = set()
    for sub in all_subs:
        bid = str(sub.condition.get("broadcaster_user_id", ""))
        if bid and bid in twitch_id_to_user:
            if sub.type == "stream.online":
                online_present.add(bid)
            elif sub.type == "stream.offline":
                offline_present.add(bid)

    failed_count = 0
    for user in users:
        tid = str(user.twitch_id)
        if tid in online_present and tid in offline_present:
            continue
        if await _reconcile_user_subs(user, tid in online_present, tid in offline_present, twitch, db_session_factory):
            failed_count += 1

    logger.info(
        "reconcile_stream_subscriptions: проверено %d юзеров, online_missing=%d offline_missing=%d failed=%d",
        len(users),
        sum(1 for u in users if str(u.twitch_id) not in online_present),
        sum(1 for u in users if str(u.twitch_id) not in offline_present),
        failed_count,
    )


async def clear_telegram_chat_binding(
    db_session_factory,
    chat_id: str,
    user_id: int | None = None,
    scope: str | None = None,
) -> str | None:
    """Очистить привязку Telegram-чата в БД по chat_id (или user_id+scope).

    Используется из ``handle_chat_disconnected`` (MQTT-событие от TG-бота)
    и из check-эндпоинта ``/api/user/check/telegram`` (при детекте кика).

    Возвращает очищенный scope ("stream"|"clips"|"stickers") или None, если
    привязка не найдена. Для scope="stream" снимает EventSub-подписки.
    """
    tg_user_id: int | None = None
    cleared_scope: str | None = None
    async with db_session_factory() as db:
        stmt = sa.select(TelegramSettings)
        if user_id is not None and scope is not None:
            stmt = stmt.where(TelegramSettings.user_id == user_id)
        else:
            stmt = stmt.where(
                sa.or_(
                    TelegramSettings.stream_chat_id == chat_id,
                    TelegramSettings.clips_chat_id == chat_id,
                    TelegramSettings.stickers_chat_id == chat_id,
                )
            )
        result = await db.execute(stmt)
        tg = result.scalar_one_or_none()
        if tg is None:
            logger.info("clear_telegram_chat_binding: привязка не найдена (chat_id=%s)", chat_id)
            return None

        if scope is not None:
            # Проверяем, что chat_id в БД совпадает с payload — иначе можем
            # очистить новую привязку, если юзер переподключил чат.
            expected_col = _SCOPE_COLUMNS[scope][0]
            if getattr(tg, expected_col) != chat_id:
                logger.warning(
                    "clear_telegram_chat_binding: chat_id не совпадает для scope=%s "
                    "(stored=%s payload=%s) — пропускаем",
                    scope,
                    getattr(tg, expected_col),
                    chat_id,
                )
                return None
            cleared_scope = scope
        else:
            # Сопоставляем по chat_id среди всех scope.
            if tg.stream_chat_id == chat_id:
                cleared_scope = "stream"
            elif tg.clips_chat_id == chat_id:
                cleared_scope = "clips"
            elif tg.stickers_chat_id == chat_id:
                cleared_scope = "stickers"

        if cleared_scope is None:
            return None

        col_id, col_type, col_title, col_at = _SCOPE_COLUMNS[cleared_scope]
        setattr(tg, col_id, None)
        setattr(tg, col_type, None)
        setattr(tg, col_title, None)
        setattr(tg, col_at, None)

        if cleared_scope == "stream":
            tg.stream_notification_enabled = False
            tg.last_stream_message_id = None
            tg.twitch_to_tg_enabled = False
            tg.tg_to_twitch_enabled = False
        elif cleared_scope == "clips":
            tg.clips_enabled = False
        elif cleared_scope == "stickers":
            tg.stickers_enabled = False

        tg_user_id = tg.user_id
        await db.commit()

    logger.info(
        "clear_telegram_chat_binding: очищен scope=%s chat_id=%s user_id=%s",
        cleared_scope,
        chat_id,
        tg_user_id,
    )

    # Для stream снимаем EventSub-подписки вне сессии БД.
    if cleared_scope == "stream":
        await _unsubscribe_stream_events(tg_user_id, db_session_factory)

    return cleared_scope


async def _unsubscribe_stream_events(user_id: int, db_session_factory) -> None:
    """Отписаться от stream.online/offline EventSub для пользователя."""
    from container_runtime import get_container

    container = get_container()
    twitch = container.twitch()

    async with db_session_factory() as db:
        user = (await db.execute(sa.select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        logger.warning("_unsubscribe_stream_events: user_id=%s не найден", user_id)
        return
    try:
        await twitch.unsubscribe_stream_online(user)
        await twitch.unsubscribe_stream_offline(user)
        logger.info("_unsubscribe_stream_events: отписки выполнены для user_id=%s", user_id)
    except Exception:
        logger.error("_unsubscribe_stream_events: ошибка отписки для user_id=%s", user_id, exc_info=True)


async def handle_chat_disconnected(payload: dict[str, Any], db_session_factory) -> None:
    """Обработать MQTT-событие telegram/chat_disconnected от TG-бота.

    Бот кикнут/удалён из чата или покинул чат по запросу. Очищаем привязку
    в БД, чтобы в панели снова отображалось «Чат: не подключён».
    """
    chat_id = str(payload.get("chat_id", ""))
    if not chat_id:
        logger.warning("chat_disconnected: нет chat_id в payload: %s", payload)
        return

    user_id = payload.get("user_id")
    scope = payload.get("scope")

    logger.info(
        "chat_disconnected: chat_id=%s user_id=%s scope=%s",
        chat_id,
        user_id,
        scope,
    )

    await clear_telegram_chat_binding(db_session_factory, chat_id, user_id, scope)
