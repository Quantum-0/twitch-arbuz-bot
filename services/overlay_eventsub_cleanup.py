"""Cleanup job для overlay-managed EventSub подписок.

Сканирует Redis-множество пользователей с overlay-подписками.
Если heartbeat-key истёк и нет SSE-клиентов на канале TWITCH_EVENTS —
отписывается от follow/subscribe/subscription.message.
Raid отписывается только если enable_shoutout_on_raid выключен.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from database.models import User
from services.cache import Cache
from services.sse_manager import SSEManager
from twitch.client.twitch import Twitch
from utils.enums import SSEChannel

logger = logging.getLogger(__name__)

REDIS_USERS_SET = "eventsub:overlay:users"
REDIS_HEARTBEAT_PREFIX = "eventsub:overlay:"

OVERLAY_MANAGED_TYPES = (
    "channel.follow",
    "channel.subscribe",
    "channel.subscription.message",
)


async def _cleanup_user(
    twitch_id: str,
    twitch: Twitch,
    sse_manager: SSEManager,
    cache: Cache,
    db_session_factory,
) -> bool:
    """Возвращает True если пользователя нужно удалить из множества."""
    try:
        int(twitch_id)
    except (TypeError, ValueError):
        return True

    heartbeat_key = f"{REDIS_HEARTBEAT_PREFIX}{twitch_id}"
    if await cache.get_str(heartbeat_key) is not None:
        return False

    broadcaster_id = int(twitch_id)
    if await sse_manager.has_clients(broadcaster_id, SSEChannel.TWITCH_EVENTS):
        return False

    async with db_session_factory() as db:
        user = (
            await db.execute(sa.select(User).where(User.twitch_id == twitch_id).options(selectinload(User.settings)))
        ).scalar_one_or_none()

    if user is None:
        return True

    for sub_type in OVERLAY_MANAGED_TYPES:
        try:
            await twitch.unsubscribe_by_type(user, sub_type)
            logger.info("cleanup: unsubscribed %s for user %s", sub_type, user.login_name)
        except Exception:
            logger.warning("cleanup: failed to unsubscribe %s for %s", sub_type, user.login_name, exc_info=True)

    if user.settings and not user.settings.enable_shoutout_on_raid:
        try:
            await twitch.unsubscribe_by_type(user, "channel.raid")
            logger.info("cleanup: unsubscribed channel.raid for user %s", user.login_name)
        except Exception:
            logger.warning("cleanup: failed to unsubscribe raid for %s", user.login_name, exc_info=True)

    return True


async def cleanup_overlay_eventsub(
    twitch: Twitch,
    sse_manager: SSEManager,
    cache: Cache,
    db_session_factory,
) -> None:
    """Отписаться от overlay-EventSub подписок для неактивных оверлеев."""
    raw_users = await cache.get_set(REDIS_USERS_SET)
    if not raw_users:
        return

    twitch_ids: set[str] = {u.decode() if isinstance(u, bytes) else u for u in raw_users}
    to_remove: set[str] = set()

    for twitch_id in twitch_ids:
        if await _cleanup_user(twitch_id, twitch, sse_manager, cache, db_session_factory):
            to_remove.add(twitch_id)

    if to_remove:
        remaining = twitch_ids - to_remove
        await cache.set_set(REDIS_USERS_SET, remaining, ttl=24 * 60 * 60)
        logger.info("cleanup: removed %d users from overlay eventsub set", len(to_remove))
