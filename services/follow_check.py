import logging

import sqlalchemy as sa

from container_runtime import get_container
from database.database import AsyncSessionLocal
from database.models import User

logger = logging.getLogger(__name__)

# TTL throttle-ключа в Redis: пока ключ существует, фоновую проверку не запускаем.
FOLLOW_CHECK_TTL_SECONDS = 30 * 60


async def _get_admin_twitch_id() -> str | None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(sa.select(User.twitch_id).where(User.login_name == "quantum075"))
        return result.scalar_one_or_none()


async def refresh_followed_to_admin(user_id: int) -> None:
    """Фоновая перепроверка: зафолловлен ли пользователь на канал создателя бота.

    Throttle через Redis (30 мин): независимо от текущего значения в БД (null/true/false),
    Twitch API дёргается не чаще раза в 30 минут на пользователя. Результат записывается
    в User.followed_to_admin, что позволяет отслеживать follow/unfollow со временем.
    """
    cache = get_container().cache()
    throttle_key = f"follow_admin_check:{user_id}"
    if await cache.get_str(throttle_key) is not None:
        return  # недавно уже проверяли — пропускаем

    # Сразу ставим throttle, чтобы параллельные запросы не дублировали вызов API.
    await cache.set_str(throttle_key, "1", ttl=FOLLOW_CHECK_TTL_SECONDS)

    admin_twitch_id = await _get_admin_twitch_id()
    if not admin_twitch_id:
        logger.warning("Admin user quantum075 not found in DB, skip follow check")
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(sa.select(User.twitch_id).where(User.id == user_id))
        user_twitch_id = result.scalar_one_or_none()
        if not user_twitch_id:
            return

        try:
            twitch = get_container().twitch()
            follows = await twitch.check_user_follows_admin(admin_twitch_id, user_twitch_id)
        except Exception:
            logger.error("Error checking follow status for user_id=%s", user_id, exc_info=True)
            return

        await db.execute(sa.update(User).where(User.id == user_id).values(followed_to_admin=follows))
        await db.commit()
        logger.info("Updated followed_to_admin=%s for user_id=%s", follows, user_id)
