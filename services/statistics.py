"""Подсистема сбора и агрегации метрик мониторинга сервиса.

Счётчики инкрементятся в Redis-хэшах (один хэш на 10-минутный бакет),
а раз в 10 минут фоновый APScheduler-джоб ``flush_to_db`` заливает накопленные
значения батчем в таблицу ``statistics`` (``ON CONFLICT DO UPDATE``).

Уникальный индекс ``statistics_pk`` построен с ``NULLS NOT DISTINCT``, поэтому
строки с ``channel_id=NULL`` корректно схлопываются при повторном INSERT.

Все методы инкремента — fire-and-forget: вызываются через ``asyncio.create_task``
в точках инкремента (см. ``twitch/chat/bot.py``, ``services/eventsub_service.py``),
чтобы не блокировать основную обработку сообщений/наград.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
import sqlalchemy as sa
from opentelemetry import trace
from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Statistics
from schemas.api import StatsPeriod, StatsType

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


# Длительность одного бакета в БД (и шаг инкрементных хэшей в Redis).
BUCKET_SECONDS = 10 * 60

# Срок хранения исторических данных в БД.
RETENTION_DAYS = 3 * 30  # ~3 месяца

# Параметры агрегации графиков: (шаг в секундах, максимальный запрашиваемый диапазон).
# Границы периода выбраны так, чтобы объём ответа API оставался разумным.
PERIOD_CONFIG: dict[StatsPeriod, tuple[int, timedelta]] = {
    StatsPeriod.TEN_MIN: (10 * 60, timedelta(days=1)),
    StatsPeriod.ONE_HOUR: (60 * 60, timedelta(days=5)),
    StatsPeriod.THREE_HOURS: (3 * 60 * 60, timedelta(days=14)),
    StatsPeriod.SIX_HOURS: (6 * 60 * 60, timedelta(days=30)),
    StatsPeriod.ONE_DAY: (24 * 60 * 60, timedelta(days=90)),
}

# Множество типов метрик, для которых ``sum_ms`` несёт смысл (timing-метрики).
# Для них ``get_chart`` возвращает avg = sum_ms / count, а не sum(count).
TIMING_TYPES: set[str] = {str(StatsType.MESSAGE_PROCESSING_TIME)}


def _floor_to_bucket(dt: datetime, step_seconds: int) -> datetime:
    """Округляет ``dt`` вниз до начала бакета длиной ``step_seconds`` (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    seconds = int((dt - epoch).total_seconds())
    floored = seconds - (seconds % step_seconds)
    return epoch + timedelta(seconds=floored)


def _field_for(type_: str, subtype: str, channel_id: int | None) -> str:
    """Кодирует (type, subtype, channel_id) в имя поля Redis-хэша."""
    return f"{type_}:{subtype}:{'' if channel_id is None else channel_id}"


def _parse_field(field: str) -> tuple[str, str, int | None]:
    """Обратное преобразование имени поля хэша в кортеж."""
    type_, subtype, channel = field.split(":", 2)
    channel_id: int | None = None
    if channel != "":
        try:
            channel_id = int(channel)
        except ValueError:
            channel_id = None
    return type_, subtype, channel_id


class StatisticsService:
    """Сервис агрегации метрик мониторинга: Redis-накопитель + Postgres-дампер.

    Не хранит никаких долгоживущих счётчиков в памяти процесса — все инкременты
    идут напрямую в Redis через ``hincrby`` (атомарно, O(1)). Это позволяет
    запускать несколько инстансов бота без координации: каждый инкрементит общий
    Redis, а ``flush_to_db`` джоб (один на всю установку, т.к. планировщик с
    SQLAlchemyJobStore) заливает накопленное в БД.
    """

    HASH_KEY_PREFIX = "statistics:bucket:"
    # TTL на flush-ключи в Redis — чтобы при сбоях дампа мусор не копился.
    HASH_TTL_SECONDS = 2 * 60 * 60  # 2 часа

    def __init__(self, db_session_factory: Callable[[], AsyncSession]) -> None:
        self._db = db_session_factory
        self._r: Redis | None = None
        # Храним ссылки на fire-and-forget таски, чтобы их не убил GC.
        self._tasks: set[asyncio.Task[Any]] = set()

    async def startup(self, redis: aioredis.Redis) -> None:
        self._r = redis
        logger.info("StatisticsService started")

    # ------------------------------------------------------------------
    # Инкремент
    # ------------------------------------------------------------------

    def inc(
        self,
        type_: str | StatsType,
        subtype: str = "",
        channel_id: int | None = None,
    ) -> None:
        """Fire-and-forget инкремент счётчика.

        Безопасно вызывать из горячих путей обработки сообщений: не блокирует
        вызывающий код. Исключения логируются (не всплывают), чтобы падение
        Redis не роняло обработку чата.
        """
        if self._r is None:
            # Сервис ещё не стартовал (или Redis умер при init) — теряем метрику.
            return
        coro = self._inc(str(type_), subtype, channel_id)
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def inc_timing(
        self,
        type_: str | StatsType,
        subtype: str = "",
        value_ms: int = 0,
        channel_id: int | None = None,
    ) -> None:
        """Fire-and-forget замер времени: инкремент count и sum_ms.

        Аналогично ``inc``, но в Redis-хэше инкрементит сразу два поля — ``count``
        (число замеров) и ``sum_ms`` (суммарное время в мс). При ``flush_to_db``
        оба поля пишутся в БД; ``get_chart`` для timing-метрик возвращает avg =
        ``sum_ms / count`` вместо суммы count.
        """
        if self._r is None:
            return
        coro = self._inc_timing(str(type_), subtype, channel_id, value_ms)
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _inc(self, type_: str, subtype: str, channel_id: int | None) -> None:
        if self._r is None:
            return
        try:
            bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
            key = self._bucket_key(bucket)
            field = _field_for(type_, subtype, channel_id)
            pipe = self._r.pipeline(transaction=False)
            pipe.hincrby(key, field, 1)
            pipe.expire(key, self.HASH_TTL_SECONDS)
            await pipe.execute()
        except Exception:
            logger.error("Statistics inc failed for type=%s subtype=%s", type_, subtype, exc_info=True)

    async def _inc_timing(
        self, type_: str, subtype: str, channel_id: int | None, value_ms: int
    ) -> None:
        if self._r is None:
            return
        try:
            bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
            key = self._bucket_key(bucket)
            count_field = _field_for(type_, subtype, channel_id)
            sum_ms_field = _field_for(f"{type_}:sum_ms", subtype, channel_id)
            pipe = self._r.pipeline(transaction=False)
            pipe.hincrby(key, count_field, 1)
            pipe.hincrby(key, sum_ms_field, value_ms)
            pipe.expire(key, self.HASH_TTL_SECONDS)
            await pipe.execute()
        except Exception:
            logger.error(
                "Statistics inc_timing failed for type=%s subtype=%s",
                type_,
                subtype,
                exc_info=True,
            )

    def _bucket_key(self, bucket: datetime) -> str:
        return self.HASH_KEY_PREFIX + bucket.strftime("%Y-%m-%dT%H:%M:%S")

    # ------------------------------------------------------------------
    # Дамп в БД
    # ------------------------------------------------------------------

    @tracer.start_as_current_span("Statistics: flush to DB")
    async def flush_to_db(self) -> None:
        """Сливает все завершённые бакеты из Redis в таблицу ``statistics``.

        Бакеты «старше текущего» (т.е. уже не накапливающие данные) выбираются
        через SCAN, для каждого ``HGETALL`` + батч ``INSERT ... ON CONFLICT DO
        UPDATE``. Текущий (ещё живой) бакет пропускается — его польют в следующий
        запуск. Идемпотентен: повторный запуск по тем же ключам безопасен благодаря
        ``ON CONFLICT`` (данные в Redis не удаляются до успешного INSERT).
        """
        if self._r is None:
            logger.warning("StatisticsService.flush_to_db called before startup")
            return
        current_bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
        try:
            keys = [k async for k in self._r.scan_iter(match=f"{self.HASH_KEY_PREFIX}*")]
        except Exception:
            logger.error("Statistics flush: scan_iter failed", exc_info=True)
            return

        for key in keys:
            if isinstance(key, bytes):
                key_str = key.decode("utf-8")
            else:
                key_str = key
            bucket = self._parse_bucket_key(key_str)
            if bucket is None:
                continue
            if bucket >= current_bucket:
                # Текущий бакет ещё накапливается — пропускаем.
                continue
            await self._dump_one(key_str, bucket)

    async def _dump_one(self, key: str, bucket: datetime) -> None:
        if self._r is None:
            return
        try:
            raw = await self._r.hgetall(key)
        except Exception:
            logger.error("Statistics flush: HGETALL failed for %s", key, exc_info=True)
            return
        if not raw:
            return
        rows = self._build_rows_from_hash(raw, bucket)
        if not rows:
            return
        try:
            async with self._db() as session:
                stmt = (
                    pg_insert(Statistics)
                    .values(rows)
                    .on_conflict_do_update(
                        index_elements=["bucket_ts", "type", "subtype", "channel_id"],
                        set_={
                            "count": Statistics.__table__.c.count + pg_insert(Statistics).excluded.count,
                            "sum_ms": Statistics.__table__.c.sum_ms + pg_insert(Statistics).excluded.sum_ms,
                        },
                    )
                )
                await session.execute(stmt)
                await session.commit()
            await self._r.delete(key)
        except Exception:
            logger.error("Statistics flush: DB insert failed for bucket=%s", bucket.isoformat(), exc_info=True)

    @staticmethod
    def _build_rows_from_hash(
        raw: dict[Any, Any], bucket: datetime
    ) -> list[dict[str, Any]]:
        """Превращает HGETALL-ответ Redis в список строк для INSERT.

        Timing-метрики пишутся через ``inc_timing`` двумя полями: ``count`` и
        ``<type>:sum_ms``. Здесь они джойнятся обратно по ``(type, subtype, channel_id)``.
       """
        # Сначала разбиваем поля на count- и sum_ms-карты по общему ключу.
        counts: dict[tuple[str, str, int | None], int] = {}
        sum_ms: dict[tuple[str, str, int | None], int] = {}
        for field, value in raw.items():
            if isinstance(field, bytes):
                field = field.decode("utf-8")
            if isinstance(value, bytes):
                value_str = value.decode("utf-8")
            else:
                value_str = value
            try:
                ivalue = int(value_str)
            except ValueError:
                continue
            type_, subtype, channel_id = _parse_field(field)
            if type_.endswith(":sum_ms"):
                base_type = type_[: -len(":sum_ms")]
                sum_ms[(base_type, subtype, channel_id)] = ivalue
            else:
                counts[(type_, subtype, channel_id)] = ivalue

        # Собираем строки: для каждого count-поля — его значение; если есть
        # парный sum_ms — добавляем (timing-метрика); для count-метрик sum_ms
        # остаётся None (БД хранит default 0).
        rows: list[dict[str, Any]] = []
        for key_, count in counts.items():
            type_, subtype, channel_id = key_
            row: dict[str, Any] = {
                "bucket_ts": bucket,
                "type": type_,
                "subtype": subtype,
                "channel_id": channel_id,
                "count": count,
            }
            ms = sum_ms.pop(key_, None)
            if ms is not None:
                row["sum_ms"] = ms
            rows.append(row)
        # Оставшиеся sum_ms-поля без пары (маловероятно): пишем с count=0,
        # чтобы не потерять данные.
        for type_, subtype, channel_id in sum_ms:
            rows.append(
                {
                    "bucket_ts": bucket,
                    "type": type_,
                    "subtype": subtype,
                    "channel_id": channel_id,
                    "count": 0,
                    "sum_ms": sum_ms[(type_, subtype, channel_id)],
                }
            )
        return rows

    def _parse_bucket_key(self, key: str) -> datetime | None:
        if not key.startswith(self.HASH_KEY_PREFIX):
            return None
        ts_str = key[len(self.HASH_KEY_PREFIX) :]
        try:
            return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            logger.warning("Statistics flush: cannot parse bucket key %s", key)
            return None

    # ------------------------------------------------------------------
    # Очистка старых данных
    # ------------------------------------------------------------------

    @tracer.start_as_current_span("Statistics: retention cleanup")
    async def cleanup_old_data(self) -> None:
        """Удаляет строки старше ``RETENTION_DAYS`` (запускается раз в сутки)."""
        try:
            async with self._db() as session:
                cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
                await session.execute(
                    sa.delete(Statistics).where(Statistics.bucket_ts < cutoff)
                )
                await session.commit()
        except Exception:
            logger.error("Statistics cleanup failed", exc_info=True)

    # ------------------------------------------------------------------
    # Чтение для графика
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_range(
        dt_from: datetime | None,
        dt_to: datetime | None,
        max_window: timedelta,
    ) -> tuple[datetime, datetime]:
        """Нормализует границы диапазона к UTC и ограничивает макс. окно.

        ``dt_to`` по умолчанию = now(); ``dt_from`` = ``dt_to - max_window``.
        Если запрошенный диапазон шире ``max_window`` — ``dt_from`` сдвигается
        вперёд, чтобы не раздувать ответ API.
        """
        now_utc = datetime.now(UTC)
        if dt_to is None:
            dt_to = now_utc
        elif dt_to.tzinfo is None:
            dt_to = dt_to.replace(tzinfo=UTC)
        else:
            dt_to = dt_to.astimezone(UTC)

        if dt_from is None:
            dt_from = dt_to - max_window
        elif dt_from.tzinfo is None:
            dt_from = dt_from.replace(tzinfo=UTC)
        else:
            dt_from = dt_from.astimezone(UTC)

        if dt_to - dt_from > max_window:
            dt_from = dt_to - max_window
        return dt_to, dt_from

    @tracer.start_as_current_span("Statistics: get chart")
    async def get_chart(
        self,
        type_: str | StatsType,
        *,
        subtype: str | None = None,
        channel_id: int | None = None,
        period: StatsPeriod = StatsPeriod.TEN_MIN,
        dt_from: datetime | None = None,
        dt_to: datetime | None = None,
    ) -> list[tuple[datetime, int]]:
        """Возвращает ряд точек (bucket_ts, value) для графика.

        Для count-метрик ``value`` — сумма ``count`` внутри бакета.
        Для timing-метрик (``type_`` в ``TIMING_TYPES``) ``value`` — среднее
        время в мс = ``sum(sum_ms) / sum(count)`` (округляется до int). Если
        ``count == 0`` в бакете — ``value = 0``.

        Гарантированно заполняет нулями пустые бакеты внутри запрошенного
        диапазона (даже те, для которых в БД нет строк) — чтобы на фронте
        график был непрерывным.
        """
        step_seconds, max_window = PERIOD_CONFIG[period]
        type_str = str(type_)
        is_timing = type_str in TIMING_TYPES

        dt_to, dt_from = self._normalize_range(dt_from, dt_to, max_window)
        if dt_from >= dt_to:
            return []

        # Округляем границы к сетке бакетов.
        start = _floor_to_bucket(dt_from, step_seconds)
        end = _floor_to_bucket(dt_to, step_seconds)
        if end < dt_to:
            end = end + timedelta(seconds=step_seconds)

        rows = await self._query_rows(
            type_str=type_str,
            subtype_filter=subtype,
            channel_id=channel_id,
            start=start,
            end=end,
            step_seconds=step_seconds,
            is_timing=is_timing,
        )

        # Нормализуем ключи к началу бакета и собираем карту bucket -> value.
        values: dict[datetime, int] = {}
        for bucket_ts, value in rows:
            if bucket_ts.tzinfo is None:
                bucket_ts = bucket_ts.replace(tzinfo=UTC)
            else:
                bucket_ts = bucket_ts.astimezone(UTC)
            floored = _floor_to_bucket(bucket_ts, step_seconds)
            values[floored] = values.get(floored, 0) + value

        # Заполняем нулями пустые бакеты в диапазоне.
        result: list[tuple[datetime, int]] = []
        cur = start
        while cur < end:
            result.append((cur, values.get(cur, 0)))
            cur = cur + timedelta(seconds=step_seconds)
        return result

    async def _query_rows(
        self,
        *,
        type_str: str,
        subtype_filter: str | None,
        channel_id: int | None,
        start: datetime,
        end: datetime,
        step_seconds: int,
        is_timing: bool = False,
    ) -> list[tuple[datetime, int]]:
        """Аггерирует данные из ``statistics`` по бакетам длиной ``step_seconds``.

        Использует Postgres-функцию ``date_bin``, чтобы внутри запрошенного
        периода (например, 1d) склеить несколько 10-минутных записей в один бакет.

        Для timing-метрик (``is_timing=True``) возвращает ``sum_ms / count``
        (avg), для обычных — ``sum(count)``.
        """
        try:
            async with self._db() as session:
                bucket_expr = sa.func.date_bin(
                    sa.text(f"'{step_seconds} seconds'"),
                    Statistics.bucket_ts,
                    sa.text("timestamp '2000-01-01 00:00:00+00'"),
                ).label("bucket")
                if is_timing:
                    # avg = sum(sum_ms) / NULLIF(sum(count), 0) — для пустых бакетов NULL→0
                    value_expr = sa.func.coalesce(
                        sa.func.sum(Statistics.sum_ms)
                        / sa.func.nullif(sa.func.sum(Statistics.count), 0),
                        0,
                    ).label("value")
                else:
                    value_expr = sa.func.sum(Statistics.count).label("value")
                stmt = (
                    sa.select(bucket_expr, value_expr)
                    .where(Statistics.type == type_str)
                    .where(Statistics.bucket_ts >= start)
                    .where(Statistics.bucket_ts < end)
                    .group_by(bucket_expr)
                    .order_by(bucket_expr)
                )
                if subtype_filter is not None:
                    stmt = stmt.where(Statistics.subtype == subtype_filter)
                if channel_id is not None:
                    stmt = stmt.where(Statistics.channel_id == channel_id)
                result = await session.execute(stmt)
                return [(row[0], int(row[1] or 0)) for row in result.all()]
        except Exception:
            logger.error("Statistics get_chart query failed", exc_info=True)
            return []
