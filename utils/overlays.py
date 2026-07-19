import logging

import sqlalchemy as sa

from container_runtime import get_container
from database.models import User

logger = logging.getLogger(__name__)

# Минимальный интервал между обновлениями overlays_last_usage для одного пользователя,
# чтобы не нагружать БД при частом обращении browser-source оверлея.
OVERLAY_USAGE_UPDATE_INTERVAL = sa.text("interval '1 hour'")


async def touch_overlay_usage(
    *,
    channel_id: int | str | None = None,
    channel_name: str | None = None,
) -> None:
    """Отмечает факт обращения к оверлею, обновляя overlays_last_usage.

    Обновление выполняется одним UPDATE с условием: обновляем только если
    предыдущее значение NULL либо старее 1 часа — это гарантирует не более одной
    записи на пользователя в час даже при частых запросах от OBS browser-source.
    """
    if channel_id is None and channel_name is None:
        return

    try:
        session_factory = get_container().db_session_factory()
        async with session_factory() as db:
            stmt = (
                sa.update(User)
                .values(overlays_last_usage=sa.func.now())
                .where(
                    sa.or_(
                        User.overlays_last_usage.is_(None),
                        User.overlays_last_usage < sa.func.now() - OVERLAY_USAGE_UPDATE_INTERVAL,
                    )
                )
            )
            if channel_id is not None:
                stmt = stmt.where(User.twitch_id == str(channel_id))
            else:
                stmt = stmt.where(User.login_name == channel_name)
            await db.execute(stmt)
            await db.commit()
    except Exception:
        logger.exception(
            "Failed to touch overlay usage for channel_id=%s channel_name=%s", channel_id, channel_name
        )
