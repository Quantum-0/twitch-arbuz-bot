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
from sqlalchemy.sql.elements import Label

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

# Sum-метрики: ``value = sum(sum_ms)`` (суммарный объём, не среднее). ``count``
# — число событий, ``sum_ms`` — суммарный объём (байты и т.п.). Пишутся через
# ``inc_timing`` (переиспользуем механику двух полей в Redis-хэше).
SUM_TYPES: set[str] = {str(StatsType.HEAT_PROXY_BYTES)}

# Gauge-метрики: ``value = avg(count)`` внутри бакета агрегации. Хранят
# мгновенное значение (snapshot), в Redis пишутся через ``hset`` (overwrite, не
# инкремент), в БД — через ``ON CONFLICT DO UPDATE set count = EXCLUDED.count``
# (перезапись, не суммирование). Сейчас: только SSE-подключения.
GAUGE_TYPES: set[str] = {str(StatsType.SSE_CONNECTIONS)}


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


def _value_expr_for(type_str: str) -> Label[Any]:
    """SQL-выражение для агрегированного ``value`` в зависимости от категории.

    - **Counter** (по умолчанию): ``sum(count)``.
    - **Gauge** (``GAUGE_TYPES``): ``avg(count)`` — мгновенное значение за бакет
      усредняется при схлопывании нескольких бакетов в один (period > 10m).
    - **Timing** (``TIMING_TYPES``): ``sum(sum_ms) / sum(count)`` (avg).
    - **Sum** (``SUM_TYPES``): ``sum(sum_ms)`` (суммарный объём, не среднее).
    """
    if type_str in GAUGE_TYPES:
        return sa.func.coalesce(sa.func.avg(Statistics.count), 0).label("value")
    if type_str in TIMING_TYPES:
        return sa.func.coalesce(
            sa.func.sum(Statistics.sum_ms) / sa.func.nullif(sa.func.sum(Statistics.count), 0),
            0,
        ).label("value")
    if type_str in SUM_TYPES:
        return sa.func.coalesce(sa.func.sum(Statistics.sum_ms), 0).label("value")
    return sa.func.coalesce(sa.func.sum(Statistics.count), 0).label("value")


def _needs_empty_subtype_filter(type_str: str) -> bool:
    """Гарантирует, что timing/sum-метрики не подтянут мусорные строки.

    Старый баг кодировки (до исправления ``SUM_MS_SUFFIX``) оставил в БД строки с
    ``subtype="sum_ms"``; для timing/sum-метрик нужно фильтровать ``subtype=""``.
    """
    return type_str in TIMING_TYPES or type_str in SUM_TYPES


# Суффикс, добавляемый к ``type_`` в имени Redis-поля для ``sum_ms`` timing-метрик.
# Не содержит двоеточия, чтобы ``_parse_field`` не разбивал его на отдельный
# subtype (баг, из-за которого sum_ms попадал в БД как строка с subtype="sum_ms"
# и записывался в колонку count). Распознаётся в ``_build_rows_from_hash`` через
# ``endswith(SUM_MS_SUFFIX)``.
SUM_MS_SUFFIX = "__sum_ms"


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
    # Префикс для Redis-множеств (SET) активных каналов: один SET на бакет и
    # направление (incoming/outgoing), член множества — twitch_id канала.
    # SCARD даёт число уникальных каналов, которое пишется в БД как count.
    CHANNELS_KEY_PREFIX = "statistics:channels:"
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
        amount: int = 1,
    ) -> None:
        """Fire-and-forget инкремент счётчика на ``amount``.

        Безопасно вызывать из горячих путей обработки сообщений: не блокирует
        вызывающий код. Исключения логируются (не всплывают), чтобы падение
        Redis не роняло обработку чата. ``amount`` по умолчанию = 1; для
        byte-счётчиков можно передать размер сообщения.
        """
        if self._r is None:
            # Сервис ещё не стартовал (или Redis умер при init) — теряем метрику.
            return
        coro = self._inc(str(type_), subtype, channel_id, amount)
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

        Та же механика используется для sum-метрик (``SUM_TYPES``), где ``count``
        — число событий, а ``sum_ms`` — суммарный объём (например, байты Heat).
        """
        if self._r is None:
            return
        coro = self._inc_timing(str(type_), subtype, channel_id, value_ms)
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _inc(self, type_: str, subtype: str, channel_id: int | None, amount: int = 1) -> None:
        if self._r is None:
            return
        try:
            bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
            key = self._bucket_key(bucket)
            field = _field_for(type_, subtype, channel_id)
            pipe = self._r.pipeline(transaction=False)
            pipe.hincrby(key, field, amount)
            pipe.expire(key, self.HASH_TTL_SECONDS)
            await pipe.execute()
        except Exception:
            logger.error("Statistics inc failed for type=%s subtype=%s", type_, subtype, exc_info=True)

    async def _inc_timing(self, type_: str, subtype: str, channel_id: int | None, value_ms: int) -> None:
        if self._r is None:
            return
        try:
            bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
            key = self._bucket_key(bucket)
            count_field = _field_for(type_, subtype, channel_id)
            # sum_ms-поле: type_ + SUM_MS_SUFFIX (без двоеточия внутри), чтобы
            # _parse_field не разбил суффикс в отдельный subtype (старый баг:
            # sum_ms попадал в БД как строка с subtype="sum_ms" и писался в count).
            sum_ms_field = _field_for(f"{type_}{SUM_MS_SUFFIX}", subtype, channel_id)
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

    def _channels_key(self, subtype: str, bucket: datetime) -> str:
        """Ключ SET-а уникальных каналов для направления ``subtype`` и бакета."""
        return self.CHANNELS_KEY_PREFIX + subtype + ":" + bucket.strftime("%Y-%m-%dT%H:%M:%S")

    # ------------------------------------------------------------------
    # Уникальные каналы (SET-based метрика)
    # ------------------------------------------------------------------

    def mark_channel(
        self,
        subtype: str,
        channel_id: int,
    ) -> None:
        """Fire-and-forget: добавляет ``channel_id`` в SET уникальных каналов.

        Используется для метрики ``ACTIVE_CHANNELS``: в конце бакета ``SCARD``
        даёт число уникальных каналов, на которых были сообщения (входящие или
        исходящие — определяется ``subtype``).
        """
        if self._r is None:
            return
        coro = self._mark_channel(subtype, channel_id)
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _mark_channel(self, subtype: str, channel_id: int) -> None:
        if self._r is None:
            return
        try:
            bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
            key = self._channels_key(subtype, bucket)
            pipe = self._r.pipeline(transaction=False)
            pipe.sadd(key, str(channel_id))
            pipe.expire(key, self.HASH_TTL_SECONDS)
            await pipe.execute()
        except Exception:
            logger.error(
                "Statistics mark_channel failed for subtype=%s channel=%s",
                subtype,
                channel_id,
                exc_info=True,
            )

    def set_gauge(
        self,
        type_: str | StatsType,
        values: dict[str, int],
    ) -> None:
        """Fire-and-forget snapshot gauge-метрики: перезаписывает значения в Redis.

        ``values`` — словарь ``{subtype: value}``. Для каждого подтипа в Redis-хэше
        текущего бакета поле ``{type}:{subtype}:`` перезаписывается (``hset``, не
        ``hincrby``) — gauge хранит мгновенное значение, а не накопленную сумму.
        ``flush_to_db`` для gauge-метрик использует
        ``ON CONFLICT DO UPDATE set count = EXCLUDED.count`` (перезапись, не +).

        Используется для SSE-подключений: раз в минуту APScheduler-джоб делает
        snapshot ``SSEManager`` и вызывает этот метод.
        """
        if self._r is None:
            return
        if not values:
            return
        coro = self._set_gauge(str(type_), values)
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _set_gauge(self, type_: str, values: dict[str, int]) -> None:
        if self._r is None:
            return
        try:
            bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
            key = self._bucket_key(bucket)
            mapping: dict[str, str] = {
                _field_for(type_, subtype, None): str(v) for subtype, v in values.items() if v != 0
            }
            # Удаляем нулевые подтипы (если значение сбросилось в 0): иначе в БД
            # попадёт мусор, а zero-fill в get_chart отрисует как 0.
            zero_fields = [_field_for(type_, s, None) for s, v in values.items() if v == 0]
            pipe = self._r.pipeline(transaction=False)
            if mapping:
                pipe.hset(key, mapping=mapping)  # type: ignore[arg-type]
            if zero_fields:
                pipe.hdel(key, *zero_fields)
            pipe.expire(key, self.HASH_TTL_SECONDS)
            await pipe.execute()
        except Exception:
            logger.error("Statistics set_gauge failed for type=%s", type_, exc_info=True)

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

        Аналогично обрабатываются SET-ключи ``statistics:channels:*``: для каждого
        ``SCARD`` (число уникальных каналов) записывается в ``count`` с
        ``type=active_channels`` и ``subtype=incoming``/``outgoing``.
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

        # SET-метрика активных каналов.
        await self._flush_channels(current_bucket)

    async def _flush_channels(self, current_bucket: datetime) -> None:
        """Обрабатывает ``statistics:channels:*`` SET-ключи и заливает SCARD в БД."""
        if self._r is None:
            return
        try:
            ch_keys = [k async for k in self._r.scan_iter(match=f"{self.CHANNELS_KEY_PREFIX}*")]
        except Exception:
            logger.error("Statistics flush: channels scan_iter failed", exc_info=True)
            return
        for key in ch_keys:
            if isinstance(key, bytes):
                key_str = key.decode("utf-8")
            else:
                key_str = key
            subtype, bucket = self._parse_channels_key(key_str)
            if subtype is None or bucket is None:
                continue
            if bucket >= current_bucket:
                continue
            await self._dump_channels_set(key_str, subtype, bucket)

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
        # Разделяем gauge (перезапись) и counter (сумма) строки.
        gauge_rows = [r for r in rows if r["type"] in GAUGE_TYPES]
        counter_rows = [r for r in rows if r["type"] not in GAUGE_TYPES]
        try:
            async with self._db() as session:
                if counter_rows:
                    stmt_counter = (
                        pg_insert(Statistics)
                        .values(counter_rows)
                        .on_conflict_do_update(
                            index_elements=["bucket_ts", "type", "subtype", "channel_id"],
                            set_={
                                "count": Statistics.__table__.c.count + pg_insert(Statistics).excluded.count,
                                "sum_ms": Statistics.__table__.c.sum_ms + pg_insert(Statistics).excluded.sum_ms,
                            },
                        )
                    )
                    await session.execute(stmt_counter)
                if gauge_rows:
                    # Gauge: перезаписываем count, не суммируем (мгновенное значение).
                    stmt_gauge = (
                        pg_insert(Statistics)
                        .values(gauge_rows)
                        .on_conflict_do_update(
                            index_elements=["bucket_ts", "type", "subtype", "channel_id"],
                            set_={"count": pg_insert(Statistics).excluded.count},
                        )
                    )
                    await session.execute(stmt_gauge)
                await session.commit()
            await self._r.delete(key)
        except Exception:
            logger.error("Statistics flush: DB insert failed for bucket=%s", bucket.isoformat(), exc_info=True)

    @staticmethod
    def _build_rows_from_hash(raw: dict[Any, Any], bucket: datetime) -> list[dict[str, Any]]:
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
            if type_.endswith(SUM_MS_SUFFIX):
                base_type = type_[: -len(SUM_MS_SUFFIX)]
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

    def _parse_channels_key(self, key: str) -> tuple[str | None, datetime | None]:
        """Парсит ``statistics:channels:<subtype>:<ISO>`` → (subtype, bucket)."""
        if not key.startswith(self.CHANNELS_KEY_PREFIX):
            return None, None
        rest = key[len(self.CHANNELS_KEY_PREFIX) :]
        # subtype не содержит ":", поэтому split(":", 1) корректен.
        if ":" not in rest:
            return None, None
        subtype, ts_str = rest.split(":", 1)
        try:
            bucket = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            logger.warning("Statistics flush: cannot parse channels key %s", key)
            return None, None
        return subtype, bucket

    async def _dump_channels_set(self, key: str, subtype: str, bucket: datetime) -> None:
        """Считает SCARD и пишет строку с type=active_channels, count=scard."""
        if self._r is None:
            return
        try:
            count = await self._r.scard(key)
        except Exception:
            logger.error("Statistics flush: SCARD failed for %s", key, exc_info=True)
            return
        if count == 0:
            # Пустой SET (не было сообщений) — не пишем строку, чтобы не плодить
            # нули; zero-fill в get_chart отрисует пропуск как 0.
            await self._r.delete(key)
            return
        try:
            async with self._db() as session:
                stmt = (
                    pg_insert(Statistics)
                    .values(
                        {
                            "bucket_ts": bucket,
                            "type": str(StatsType.ACTIVE_CHANNELS),
                            "subtype": subtype,
                            "channel_id": None,
                            "count": count,
                            "sum_ms": 0,
                        }
                    )
                    .on_conflict_do_update(
                        index_elements=["bucket_ts", "type", "subtype", "channel_id"],
                        set_={"count": pg_insert(Statistics).excluded.count},
                    )
                )
                await session.execute(stmt)
                await session.commit()
            await self._r.delete(key)
        except Exception:
            logger.error(
                "Statistics flush: channels DB insert failed for bucket=%s subtype=%s",
                bucket.isoformat(),
                subtype,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Очистка старых данных
    # ------------------------------------------------------------------

    @tracer.start_as_current_span("Statistics: retention cleanup")
    async def cleanup_old_data(self) -> None:
        """Удаляет строки старше ``RETENTION_DAYS`` (запускается раз в сутки)."""
        try:
            async with self._db() as session:
                cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
                await session.execute(sa.delete(Statistics).where(Statistics.bucket_ts < cutoff))
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

    @staticmethod
    def _compute_bucket_range(
        dt_from: datetime | None,
        dt_to: datetime | None,
        max_window: timedelta,
        step_seconds: int,
    ) -> tuple[datetime, datetime] | None:
        """Нормализует диапазон и округляет к сетке бакетов + отсекает неполные.

        Возвращает ``(start, end)`` — начало первого и конец последнего
        **полностью завершённого** бакета, или ``None``, если диапазон пуст
        (``dt_from >= dt_to`` после нормализации, или все бакеты ещё не
        завершились). Вызывается из ``get_chart`` и ``get_chart_series``,
        чтобы логика отсечения будущих/неполных бакетов была единой.
        """
        dt_to, dt_from = StatisticsService._normalize_range(dt_from, dt_to, max_window)
        if dt_from >= dt_to:
            return None

        start = _floor_to_bucket(dt_from, step_seconds)
        end = _floor_to_bucket(dt_to, step_seconds)
        if end < dt_to:
            end = end + timedelta(seconds=step_seconds)

        # Отсекаем неполные и будущие бакеты: начинаем показывать только
        # полностью завершённые бакеты. ``_floor_to_bucket(now)`` — это начало
        # текущего (ещё не завершённого) бакета; все бакеты с ``bucket_ts >= end``
        # должны быть скрыты. Если user запросил ``to=0:21`` при now=0:15,
        # ``end=min(0:20, 0:10)=0:10``, и последний показанный бакет — 0:00.
        now_bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
        if end > now_bucket:
            end = now_bucket
        if start >= end:
            return None
        return start, end

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

        rng = self._compute_bucket_range(dt_from, dt_to, max_window, step_seconds)
        if rng is None:
            return []
        start, end = rng

        rows = await self._query_rows(
            type_str=type_str,
            subtype_filter=subtype,
            channel_id=channel_id,
            start=start,
            end=end,
            step_seconds=step_seconds,
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
    ) -> list[tuple[datetime, int]]:
        """Аггерирует данные из ``statistics`` по бакетам длиной ``step_seconds``.

        Использует Postgres-функцию ``date_bin``, чтобы внутри запрошенного
        периода (например, 1d) склеить несколько 10-минутных записей в один бакет.

        Категория метрики (counter/gauge/timing/sum) определяется автоматически
        через ``_value_expr_for(type_str)`` — вызывающий код не передаёт флаги.
        """
        try:
            async with self._db() as session:
                bucket_expr = sa.func.date_bin(
                    sa.text(f"'{step_seconds} seconds'"),
                    Statistics.bucket_ts,
                    sa.text("timestamp '2000-01-01 00:00:00+00'"),
                ).label("bucket")
                value_expr = _value_expr_for(type_str)
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
                elif _needs_empty_subtype_filter(type_str):
                    stmt = stmt.where(Statistics.subtype == "")
                if channel_id is not None:
                    stmt = stmt.where(Statistics.channel_id == channel_id)
                result = await session.execute(stmt)
                return [(row[0], int(row[1] or 0)) for row in result.all()]
        except Exception:
            logger.error("Statistics get_chart query failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Multi-line series (топ-N подтипов на одном графике)
    # ------------------------------------------------------------------

    @tracer.start_as_current_span("Statistics: get chart series")
    async def get_chart_series(
        self,
        type_: str | StatsType,
        *,
        channel_id: int | None = None,
        period: StatsPeriod = StatsPeriod.TEN_MIN,
        dt_from: datetime | None = None,
        dt_to: datetime | None = None,
        top_n: int = 10,
    ) -> list[tuple[str, list[tuple[datetime, int]]]]:
        """Возвращает топ-N подтипов с рядами точек для multi-line графика.

        Сначала одним запросом определяются топ-N подтипов по суммарному
        ``count`` (или ``sum_ms`` для timing-метрик) за диапазон, затем вторым
        запросом — точки для каждого из них с агрегацией ``date_bin``. Пустые
        бакеты заполняются нулями (zero-fill), чтобы все ряды имели одинаковую
        длину и были выровнены по оси X.

        Возвращает список ``(subtype, [(bucket_ts, value), ...])``, упорядоченный
        по убыванию суммарного значения подтипа (топ-N).
        """
        step_seconds, max_window = PERIOD_CONFIG[period]
        type_str = str(type_)

        rng = self._compute_bucket_range(dt_from, dt_to, max_window, step_seconds)
        if rng is None:
            return []
        start, end = rng

        if top_n < 1:
            top_n = 10

        # Шаг 1: топ-N подтипов по суммарному count/sum_ms за диапазон.
        top_subtypes = await self._query_top_subtypes(
            type_str=type_str,
            channel_id=channel_id,
            start=start,
            end=end,
            top_n=top_n,
        )
        if not top_subtypes:
            return []

        # Шаг 2: точки для каждого подтипа из топ-N.
        rows = await self._query_series_rows(
            type_str=type_str,
            subtypes=top_subtypes,
            channel_id=channel_id,
            start=start,
            end=end,
            step_seconds=step_seconds,
        )

        # Группируем по subtype и заполняем нулями пустые бакеты.
        # Сохраним исходный порядок топ-N (по убыванию суммарного count).
        result: list[tuple[str, list[tuple[datetime, int]]]] = []
        for subtype in top_subtypes:
            points_map: dict[datetime, int] = {}
            for bucket_ts, st, value in rows:
                if st != subtype:
                    continue
                if bucket_ts.tzinfo is None:
                    bucket_ts = bucket_ts.replace(tzinfo=UTC)
                else:
                    bucket_ts = bucket_ts.astimezone(UTC)
                floored = _floor_to_bucket(bucket_ts, step_seconds)
                points_map[floored] = points_map.get(floored, 0) + value
            points: list[tuple[datetime, int]] = []
            cur = start
            while cur < end:
                points.append((cur, points_map.get(cur, 0)))
                cur = cur + timedelta(seconds=step_seconds)
            result.append((subtype, points))
        return result

    async def _query_top_subtypes(
        self,
        *,
        type_str: str,
        channel_id: int | None,
        start: datetime,
        end: datetime,
        top_n: int,
    ) -> list[str]:
        """Возвращает топ-N подтипов по суммарному значению за диапазон.

        Для gauge-метрик усредняем count (мгновенные значения), для timing —
        ``sum(sum_ms)``, для sum — ``sum(sum_ms)``, для counter — ``sum(count)``.
        Топ-N определяется по «объёму» за весь диапазон.
        """
        try:
            async with self._db() as session:
                if type_str in GAUGE_TYPES:
                    value_col = sa.func.avg(Statistics.count)
                elif type_str in TIMING_TYPES or type_str in SUM_TYPES:
                    value_col = sa.func.sum(Statistics.sum_ms)
                else:
                    value_col = sa.func.sum(Statistics.count)
                stmt = (
                    sa.select(Statistics.subtype, value_col)
                    .where(Statistics.type == type_str)
                    .where(Statistics.bucket_ts >= start)
                    .where(Statistics.bucket_ts < end)
                    .group_by(Statistics.subtype)
                    .order_by(value_col.desc())
                    .limit(top_n)
                )
                if channel_id is not None:
                    stmt = stmt.where(Statistics.channel_id == channel_id)
                if _needs_empty_subtype_filter(type_str):
                    stmt = stmt.where(Statistics.subtype == "")
                result = await session.execute(stmt)
                return [row[0] for row in result.all()]
        except Exception:
            logger.error("Statistics _query_top_subtypes failed", exc_info=True)
            return []

    async def _query_series_rows(
        self,
        *,
        type_str: str,
        subtypes: list[str],
        channel_id: int | None,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> list[tuple[datetime, str, int]]:
        """Возвращает (bucket, subtype, value) для каждого подтипа из списка."""
        if not subtypes:
            return []
        try:
            async with self._db() as session:
                bucket_expr = sa.func.date_bin(
                    sa.text(f"'{step_seconds} seconds'"),
                    Statistics.bucket_ts,
                    sa.text("timestamp '2000-01-01 00:00:00+00'"),
                ).label("bucket")
                value_expr = _value_expr_for(type_str)
                stmt = (
                    sa.select(bucket_expr, Statistics.subtype, value_expr)
                    .where(Statistics.type == type_str)
                    .where(Statistics.bucket_ts >= start)
                    .where(Statistics.bucket_ts < end)
                    .where(Statistics.subtype.in_(subtypes))
                    .group_by(bucket_expr, Statistics.subtype)
                    .order_by(bucket_expr, Statistics.subtype)
                )
                if channel_id is not None:
                    stmt = stmt.where(Statistics.channel_id == channel_id)
                result = await session.execute(stmt)
                return [(row[0], row[1], int(row[2] or 0)) for row in result.all()]
        except Exception:
            logger.error("Statistics _query_series_rows failed", exc_info=True)
            return []
