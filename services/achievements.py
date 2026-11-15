"""Сервис ачивок: событийная выдача и ленивое вычисление из БД.

Два способа начисления (реестр — ``utils/achievements.py``):

- **событийные** — ``unlock`` из точки события (пасхалка, обрыв звёздочки,
  выигрыш трусов);
- **ленивые** — ``evaluate_and_get`` вызывается из страницы профиля для
  просматриваемого пользователя и пересчитывает ачивки из БД
  (лайки, покупки мемкоинов, стикеры и т.д.).

Правило заморозки: источник ленивой ачивки дёргается только пока ачивка не
достигла максимального уровня. Одноуровневая ачивка, будучи записанной,
больше не проверяется вовсе; уровневая — только до последнего уровня.
Уровень только растёт (``GREATEST`` в upsert), поэтому ачивка не может
пропасть или понизиться, даже если значение источника упало (например,
зритель снял лайк).

Все публичные методы глушат исключения (лог с ``exc_info``): ошибки ачивок
не должны ронять основной сценарий — запрос профиля, обработку награды,
розыгрыш.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    BalanceOperation,
    Links,
    MemealertsDeposit,
    MemealertsSettings,
    TelegramSettings,
    TTSSettings,
    TwitchUserSettings,
    User,
    UserAchievement,
)
from schemas.enums import BalanceOperationAction, BalanceOperationType, MemealertsDepositStatus
from utils.achievements import (
    ACHIEVEMENTS,
    LEVELED_ACHIEVEMENT_CODES,
    SOCIAL_LINK_FIELDS,
    level_for,
    to_roman,
)

logger = logging.getLogger(__name__)

# Наивный UTC-таймстемп для updated_at в upsert (как server_default модели).
_NAIVE_UTC_NOW = sa.text("(now() at time zone 'utc')")


@dataclass(frozen=True)
class AchievementView:
    """Полученная ачивка для отображения на профиле."""

    code: str
    name: str
    icon: str
    level: int
    # Римская цифра уровня; None для одноуровневых ачивок.
    roman: str | None
    unlocked_at: datetime


class AchievementsService:
    """Начисление ачивок и выдача списка для профиля."""

    def __init__(self, db_session_factory: Callable[[], AsyncSession]) -> None:
        self._db = db_session_factory

    async def unlock(self, twitch_id: str, code: str) -> bool:
        """Записать событийную ачивку. True — если ачивка новая."""
        if code not in ACHIEVEMENTS:
            logger.warning("Achievements: unknown code %r", code)
            return False
        try:
            async with self._db() as session:
                stmt = (
                    pg_insert(UserAchievement)
                    .values(twitch_id=twitch_id, code=code)
                    .on_conflict_do_nothing(index_elements=[UserAchievement.twitch_id, UserAchievement.code])
                    .returning(UserAchievement.id)
                )
                inserted = (await session.execute(stmt)).scalar_one_or_none()
                await session.commit()
                return inserted is not None
        except Exception:
            logger.warning("Achievements: unlock failed code=%s user=%s", code, twitch_id, exc_info=True)
            return False

    async def evaluate_and_get(self, user: User, likes_given: int, likes_received: int) -> list[AchievementView]:
        """Пересчитать ленивые ачивки пользователя и вернуть все полученные.

        Сортировка — новые первыми (``unlocked_at DESC``): на профиле свежие
        ачивки слева. Из ``user`` читаются только скалярные колонки;
        связанные настройки (награды, ссылки, Telegram) сервис запрашивает
        сам, чтобы не зависеть от joinedload'ов вызывающего.
        """
        try:
            async with self._db() as session:
                await self._evaluate(session, user, likes_given, likes_received)
                await session.commit()
                rows = (
                    (
                        await session.execute(
                            sa.select(UserAchievement)
                            .where(UserAchievement.twitch_id == user.twitch_id)
                            .order_by(UserAchievement.unlocked_at.desc(), UserAchievement.id.desc())
                        )
                    )
                    .scalars()
                    .all()
                )
                views = (self._to_view(row) for row in rows)
                return [v for v in views if v is not None]
        except Exception:
            logger.warning("Achievements: evaluate failed for user=%s", user.twitch_id, exc_info=True)
            return []

    async def _evaluate(self, session: AsyncSession, user: User, likes_given: int, likes_received: int) -> None:
        result = await session.execute(
            sa.select(UserAchievement.code, UserAchievement.level).where(UserAchievement.twitch_id == user.twitch_id)
        )
        stored = {row.code: row.level for row in result.all()}
        # Дешёвые значения: колонки переданного User и счётчики лайков,
        # которые страница профиля уже посчитала для себя.
        values: dict[str, int] = {
            "likes_given": likes_given,
            "likes_received": likes_received,
            "donated": int(user.donated > 0),
            "beta_tester": int(user.in_beta_test),
            "veteran": int(user.created_at <= datetime.now() - timedelta(days=365)),
            "overlay_used": int(user.overlays_last_usage is not None),
        }
        # Тяжёлые источники (запросы) — только для незамороженных ачивок.
        stale = [
            code
            for code in ("memecoins_sold", "ai_stickers_used", "all_rewards", "social_links", "telegram_linked")
            if stored.get(code, 0) < ACHIEVEMENTS[code].max_level
        ]
        values.update(await self._query_values(session, user, stale))
        for code, value in values.items():
            new_level = level_for(ACHIEVEMENTS[code].thresholds, value)
            if new_level >= 1 and new_level > stored.get(code, 0):
                await session.execute(self._upsert_stmt(user.twitch_id, code, new_level))

    async def _query_values(self, session: AsyncSession, user: User, codes: list[str]) -> dict[str, int]:
        """Значения ленивых ачивек, требующие запросов к БД."""
        values: dict[str, int] = {}
        if "memecoins_sold" in codes:
            values["memecoins_sold"] = (
                await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(MemealertsDeposit)
                    .where(
                        MemealertsDeposit.twitch_id == user.twitch_id,
                        MemealertsDeposit.status == MemealertsDepositStatus.SUCCEEDED,
                    )
                )
                or 0
            )
        if "ai_stickers_used" in codes:
            values["ai_stickers_used"] = (
                await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(BalanceOperation)
                    .where(
                        BalanceOperation.twitch_id == user.twitch_id,
                        BalanceOperation.type == BalanceOperationType.SPEND,
                        BalanceOperation.action == BalanceOperationAction.AI_STICKER,
                    )
                )
                or 0
            )
        if "all_rewards" in codes:
            row = (
                await session.execute(
                    sa.select(
                        MemealertsSettings.memealerts_reward,
                        TwitchUserSettings.ai_sticker_reward_id,
                        TTSSettings.tts_reward_id,
                    )
                    .select_from(User)
                    .outerjoin(TwitchUserSettings, TwitchUserSettings.user_id == User.id)
                    .outerjoin(MemealertsSettings, MemealertsSettings.user_id == User.id)
                    .outerjoin(TTSSettings, TTSSettings.user_id == User.id)
                    .where(User.id == user.id)
                )
            ).one_or_none()
            values["all_rewards"] = int(
                bool(row and row.memealerts_reward and row.ai_sticker_reward_id and row.tts_reward_id)
            )
        if "social_links" in codes:
            links = (await session.execute(sa.select(Links).where(Links.user_id == user.id))).scalar_one_or_none()
            values["social_links"] = int(bool(links) and all(getattr(links, f) for f in SOCIAL_LINK_FIELDS))
        if "telegram_linked" in codes:
            tg_id = await session.scalar(
                sa.select(TelegramSettings.telegram_user_id).where(TelegramSettings.user_id == user.id)
            )
            values["telegram_linked"] = int(bool(tg_id))
        return values

    @staticmethod
    def _upsert_stmt(twitch_id: str, code: str, new_level: int):
        """Монотонный upsert: уровень только растёт, updated_at — только при повышении."""
        return (
            pg_insert(UserAchievement)
            .values(twitch_id=twitch_id, code=code, level=new_level)
            .on_conflict_do_update(
                index_elements=[UserAchievement.twitch_id, UserAchievement.code],
                set_={
                    "level": sa.func.greatest(UserAchievement.level, new_level),
                    "updated_at": sa.case(
                        (new_level > UserAchievement.level, _NAIVE_UTC_NOW),
                        else_=UserAchievement.updated_at,
                    ),
                },
            )
        )

    @staticmethod
    def _to_view(row: UserAchievement) -> AchievementView | None:
        def_ = ACHIEVEMENTS.get(row.code)
        if def_ is None:  # код исчез из реестра — строку молча не показываем
            return None
        return AchievementView(
            code=row.code,
            name=def_.name,
            icon=def_.icon,
            level=row.level,
            roman=to_roman(row.level) if row.code in LEVELED_ACHIEVEMENT_CODES else None,
            unlocked_at=row.unlocked_at,
        )
