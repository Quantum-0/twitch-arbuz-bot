"""Подсистема банов: хранение, проверка и управление банами зрителей/стримеров.

Единая таблица ``bans`` покрывает три вида ограничений (см. ``schemas.enums.BanFeature``):
- global-бан зрителя — точечные фичи на всех каналах (``scope=global``, без ``site``);
- global-бан стримера — полный набор фич + ``site`` (редирект на ``/banned``);
- channel-бан — фичи на одном канале (выдал стример через панель или ``!бан``).

Проверка «забанен ли» = global-бан с фичей ИЛИ channel-бан этого канала с фичей.
Горячие пути (каждое чат-сообщение, каждая награда) идут через Redis-кэш
``bans:u:{twitch_id}`` (TTL ≤ 60 с, отрицательный кэш тоже пишется), который
инвалидируется при issue/revoke. Ключи каналов внутри кэша — twitch-ID,
поэтому проверка выражается напрямую из полей вебхука без запросов к БД
(early-reject в ``ChatBot.on_message`` до загрузки стримера).
"""

import json
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from opentelemetry import trace
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Ban, User
from schemas.enums import BanFeature, BanScope
from services.cache import Cache
from utils.reference_moderation import is_reference_moderator

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# TTL кэша банов одного таргета в Redis.
_BAN_CACHE_TTL = 60

# Верхняя граница срока бана из чата (`!бан`): 366 дней. Большей длительности
# от реальной модерации не требуется, а без ограничения гигантский токен
# (например «999999999999д») ронял обработку OverflowError'ом.
MAX_BAN_DURATION = timedelta(days=366)

# SQL-мерж фич при повторном бане (ON CONFLICT DO UPDATE): объединение JSONB-
# массивов с дедупом и сортировкой. Вычисляется на стороне БД — гонка двух
# параллельных issue() не теряет фичи (в отличие от Python-side мержа).
_MERGE_FEATURES_SQL = sa.text(
    "(SELECT COALESCE(jsonb_agg(DISTINCT e ORDER BY e), '[]'::jsonb) "
    "FROM jsonb_array_elements(bans.features || excluded.features) AS t(e))"
)
# Мерж срока: перманентный (NULL) у любой из сторон остаётся перманентным,
# иначе MAX — повторный бан не может сократить срок действующего.
_MERGE_EXPIRES_SQL = sa.text(
    "CASE WHEN bans.expires_at IS NULL OR excluded.expires_at IS NULL THEN NULL "
    "ELSE GREATEST(bans.expires_at, excluded.expires_at) END"
)


def to_naive_utc(dt: datetime) -> datetime:
    """aware → naive-UTC (колонка ``expires_at`` — ``timestamp without time zone``).

    asyncpg отказывается писать aware-datetime в naive-колонку (DataError), а
    веб-панель шлёт ISO с Z. Не-UTC offset (например +03:00) конвертируется
    корректно; naive считается уже UTC (конвенция проекта).
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


@dataclass(frozen=True)
class EffectiveBans:
    """Активные баны одного пользователя: global-фичи + фичи по каналам.

    Пустой экземпляр (``EffectiveBans.empty()``) — пользователь не забанен.
    """

    global_features: frozenset[str] = field(default_factory=frozenset)
    channel_features: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "EffectiveBans":
        return cls()

    def is_banned(self, feature: BanFeature, channel_twitch_id: str | int | None = None) -> bool:
        if feature.value in self.global_features:
            return True
        if channel_twitch_id is None:
            return False
        return feature.value in self.channel_features.get(str(channel_twitch_id), frozenset())

    def is_site_banned(self) -> bool:
        return BanFeature.SITE.value in self.global_features


class BanService:
    def __init__(
        self,
        db_session_factory: Callable[[], AsyncSession],
        cache: Cache | None = None,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._cache = cache

    # ── Проверки (горячий путь) ──────────────────────────────────────────────

    @tracer.start_as_current_span("Bans: Get effective")
    async def get_effective(self, target_twitch_id: str) -> EffectiveBans:
        """Активные баны таргета: global + все канальные (Redis-кэш → БД)."""
        cached = await self._cache_get(target_twitch_id)
        if cached is not None:
            return cached
        bans = await self._load_active_from_db(target_twitch_id)
        effective = self._to_effective(bans)
        await self._cache_put(target_twitch_id, bans, effective)
        return effective

    async def is_banned(
        self,
        target_twitch_id: str | int,
        channel_twitch_id: str | int | None,
        feature: BanFeature,
    ) -> bool:
        effective = await self.get_effective(str(target_twitch_id))
        return effective.is_banned(feature, channel_twitch_id)

    async def is_site_banned(self, target_twitch_id: str | int) -> bool:
        effective = await self.get_effective(str(target_twitch_id))
        return effective.is_site_banned()

    async def get_site_ban(self, target_twitch_id: str | int) -> Ban | None:
        """Активный global-бан с фичей ``site`` (для страницы /banned)."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                sa.select(Ban)
                .where(
                    Ban.target_twitch_id == str(target_twitch_id),
                    Ban.scope == BanScope.GLOBAL.value,
                    # JSONB @> '["site"]' — массив фич содержит site
                    Ban.features.contains([BanFeature.SITE.value]),
                    self._active_condition(),
                )
                .order_by(Ban.expires_at.is_(None).desc(), Ban.expires_at)
            )
            return result.scalars().first()

    # ── Выдача / снятие ─────────────────────────────────────────────────────

    @tracer.start_as_current_span("Bans: Issue")
    async def issue(
        self,
        *,
        target_twitch_id: str,
        target_login: str,
        target_name: str,
        features: Iterable[BanFeature],
        scope: BanScope,
        created_by_twitch_id: str,
        created_by_login: str,
        created_by_role: str,
        channel_twitch_id: str | None = None,
        channel_login: str | None = None,
        reason: str | None = None,
        expires_at: datetime | None = None,
    ) -> Ban:
        """Выдать бан (upsert: повторный бан в том же радиусе мержит фичи).

        Семантика повторного бана: фичи объединяются (SQL-side, без гонок),
        ``expires_at`` = NULL, если хоть одна из сторон перманентная, иначе
        MAX действующего и нового — повторный бан не может сократить срок.
        """
        feature_values = sorted({f.value for f in features})
        if not feature_values:
            raise ValueError("Ban must have at least one feature")
        if scope == BanScope.GLOBAL and channel_twitch_id is not None:
            raise ValueError("Global ban must not have channel_twitch_id")
        if scope == BanScope.CHANNEL and channel_twitch_id is None:
            raise ValueError("Channel ban requires channel_twitch_id")
        if scope == BanScope.CHANNEL and BanFeature.SITE.value in feature_values:
            raise ValueError("Channel ban must not restrict the 'site' feature")
        if BanFeature.SITE.value in feature_values and is_reference_moderator(target_login):
            raise ValueError("Site access cannot be restricted for service staff")

        values: dict[str, Any] = {
            "target_twitch_id": str(target_twitch_id),
            "target_login": target_login,
            "target_name": target_name,
            "scope": scope.value,
            "channel_twitch_id": str(channel_twitch_id) if channel_twitch_id is not None else None,
            "channel_login": channel_login,
            "reason": reason,
            "created_by_twitch_id": str(created_by_twitch_id),
            "created_by_login": created_by_login,
            "created_by_role": created_by_role,
            "expires_at": to_naive_utc(expires_at) if expires_at is not None else None,
        }

        async with self._db_session_factory() as session:
            stmt = (
                insert(Ban)
                .values(**values, features=feature_values)
                .on_conflict_do_update(
                    index_elements=[
                        Ban.target_twitch_id,
                        Ban.scope,
                        Ban.channel_twitch_id,
                    ],
                    set_={
                        "features": _MERGE_FEATURES_SQL,
                        "expires_at": _MERGE_EXPIRES_SQL,
                        "reason": values["reason"],
                        "created_by_twitch_id": values["created_by_twitch_id"],
                        "created_by_login": values["created_by_login"],
                        "created_by_role": values["created_by_role"],
                        "target_login": values["target_login"],
                        "target_name": values["target_name"],
                        "channel_login": values["channel_login"],
                    },
                )
                .returning(Ban)
            )
            ban = (await session.execute(stmt)).scalar_one()
            await session.commit()

        await self._invalidate_cache(values["target_twitch_id"])
        return ban

    @tracer.start_as_current_span("Bans: Revoke")
    async def revoke(self, ban_id: int) -> Ban | None:
        """Удалить бан по id. Возвращает удалённый бан или None (не найден)."""
        async with self._db_session_factory() as session:
            result = await session.execute(sa.delete(Ban).where(Ban.id == ban_id).returning(Ban))
            ban = result.scalars().one_or_none()
            await session.commit()
        if ban is not None:
            await self._invalidate_cache(ban.target_twitch_id)
        return ban

    @tracer.start_as_current_span("Bans: Revoke channel bans")
    async def revoke_channel_bans(self, target_twitch_id: str | int, channel_twitch_id: str | int) -> int:
        """Снять все баны таргета на канале (``!разбан``). Возвращает число удалённых строк."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                sa.delete(Ban)
                .where(
                    Ban.target_twitch_id == str(target_twitch_id),
                    Ban.scope == BanScope.CHANNEL.value,
                    Ban.channel_twitch_id == str(channel_twitch_id),
                )
                .returning(Ban.id)
            )
            await session.commit()
            count = len(result.scalars().all())
        if count:
            await self._invalidate_cache(target_twitch_id)
        return count

    # ── Списки (веб-API) ────────────────────────────────────────────────────

    async def list_bans(
        self,
        *,
        scope: BanScope | None = None,
        channel_twitch_id: str | None = None,
        target_twitch_id: str | None = None,
        include_expired: bool = False,
    ) -> Sequence[Ban]:
        stmt = sa.select(Ban).order_by(Ban.created_at.desc())
        if scope is not None:
            stmt = stmt.where(Ban.scope == scope.value)
        if channel_twitch_id is not None:
            stmt = stmt.where(Ban.channel_twitch_id == str(channel_twitch_id))
        if target_twitch_id is not None:
            stmt = stmt.where(Ban.target_twitch_id == str(target_twitch_id))
        if not include_expired:
            stmt = stmt.where(self._active_condition())
        async with self._db_session_factory() as session:
            return (await session.execute(stmt)).scalars().all()

    async def get_ban(self, ban_id: int, *, include_expired: bool = False) -> Ban | None:
        stmt = sa.select(Ban).where(Ban.id == ban_id)
        if not include_expired:
            stmt = stmt.where(self._active_condition())
        async with self._db_session_factory() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def is_registered_user(self, target_twitch_id: str | int) -> bool:
        """Зарегистрирован ли таргет как пользователь сайта (→ полный бан при global)."""
        async with self._db_session_factory() as session:
            return (
                await session.execute(sa.select(User.id).where(User.twitch_id == str(target_twitch_id)).limit(1))
            ).scalar_one_or_none() is not None

    # ── Внутреннее ──────────────────────────────────────────────────────────

    @staticmethod
    def _active_condition() -> sa.ColumnElement[bool]:
        # ``now() at time zone 'utc'`` — naive-UTC «сейчас», не зависящий от
        # TimeZone сессии БД (на проде MSK): expires_at хранится в naive-UTC,
        # прямое сравнение с now() трактовало бы его в локальном времени сервера.
        return sa.or_(Ban.expires_at.is_(None), Ban.expires_at > sa.text("now() at time zone 'utc'"))

    async def _load_active_from_db(self, target_twitch_id: str) -> Sequence[Ban]:
        async with self._db_session_factory() as session:
            return (
                (
                    await session.execute(
                        sa.select(Ban)
                        .where(Ban.target_twitch_id == target_twitch_id, self._active_condition())
                        .order_by(Ban.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )

    @staticmethod
    def _to_effective(bans: Sequence[Ban]) -> EffectiveBans:
        global_features: set[str] = set()
        channel_features: dict[str, set[str]] = {}
        for ban in bans:
            if ban.scope == BanScope.GLOBAL.value:
                global_features.update(ban.features or [])
            elif ban.channel_twitch_id is not None:
                channel_features.setdefault(ban.channel_twitch_id, set()).update(ban.features or [])
        return EffectiveBans(
            global_features=frozenset(global_features),
            channel_features={k: frozenset(v) for k, v in channel_features.items()},
        )

    async def _cache_get(self, target_twitch_id: str) -> EffectiveBans | None:
        if self._cache is None:
            return None
        raw = await self._cache.get_str(f"bans:u:{target_twitch_id}")
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return EffectiveBans(
                global_features=frozenset(data.get("g", [])),
                channel_features={k: frozenset(v) for k, v in data.get("c", {}).items()},
            )
        # Битая структура (не JSON, не словарь, "c" не словарь и т.п.) трактуется
        # как промах кэша: AttributeError/TypeError/ValueError → читаем из БД.
        except (ValueError, TypeError, AttributeError):
            return None

    async def _cache_put(self, target_twitch_id: str, bans: Sequence[Ban], effective: EffectiveBans) -> None:
        if self._cache is None:
            return
        # Если ближайший expires_at раньше базового TTL — кэш истекает вместе с баном.
        ttl = _BAN_CACHE_TTL
        now = datetime.utcnow()
        soonest = min(
            (b.expires_at for b in bans if b.expires_at is not None),
            default=None,
        )
        if soonest is not None:
            ttl = max(1, min(ttl, int((soonest - now).total_seconds()) + 1))
        payload = {
            "g": sorted(effective.global_features),
            "c": {k: sorted(v) for k, v in effective.channel_features.items()},
        }
        await self._cache.set_str(f"bans:u:{target_twitch_id}", json.dumps(payload), ttl=ttl)

    async def _invalidate_cache(self, target_twitch_id: str | int) -> None:
        if self._cache is not None:
            await self._cache.delete(f"bans:u:{target_twitch_id}")


def parse_ban_duration(token: str) -> timedelta | None:
    """Парсинг срока бана для ``!бан Vasya 24ч``: ``30м``/``2ч``/``3д``/``1нед`` и т.п.

    Понимает суффиксы ``с``/``м``/``ч``/``д`` и латинские ``s``/``m``/``h``/``d``,
    а также ``нед``/``w`` (неделя). Комбинированные значения (``1д12ч``) не
    поддерживаются — пишите срок одним числом с суффиксом. None — не похоже на срок.
    """
    token = token.strip().lower()
    suffixes: dict[str, timedelta] = {
        "с": timedelta(seconds=1),
        "м": timedelta(minutes=1),
        "ч": timedelta(hours=1),
        "д": timedelta(days=1),
        "нед": timedelta(weeks=1),
        "s": timedelta(seconds=1),
        "m": timedelta(minutes=1),
        "h": timedelta(hours=1),
        "d": timedelta(days=1),
        "w": timedelta(weeks=1),
    }
    for suffix, unit in sorted(suffixes.items(), key=lambda kv: -len(kv[0])):
        if token.endswith(suffix):
            number = token[: -len(suffix)]
            if not number.isdigit() or int(number) <= 0:
                continue
            try:
                return int(number) * unit
            except OverflowError:
                # Число не влезает в timedelta (>|2.7 млн лет|) — считаем
                # неразборчивым сроком, а не роняем обработку команды.
                return None
    return None


def looks_like_duration(token: str) -> bool:
    """Похоже ли на попытку указать срок (для подсказки в чате).

    ``2ч24м`` (комбинированный срок — не поддерживается), ``24 hour`` и т.п. —
    распознать как срок не выйдет, но пользователь явно его имел в виду.
    Эвристика: есть цифра и токен заканчивается буквой.
    """
    token = token.strip().lower()
    return bool(token) and any(c.isdigit() for c in token) and token[-1].isalpha()
