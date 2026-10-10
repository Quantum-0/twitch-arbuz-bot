"""Коллектор метрик нагрузки хоста для страницы «Сервер» (только модераторы).

Источники метрик выбраны так, чтобы НЕ давать контейнеру доступ к файлам хоста
(никаких ``/:/host:ro`` и ``/proc:/host/proc:ro``):

- **CPU / RAM / load** — собственный ``/proc`` контейнера: в Docker (без lxcfs)
  ``/proc/stat``, ``/proc/meminfo`` и ``/proc/loadavg`` показывают хостовые
  значения — PID-namespace виртуализирует только списки процессов;
- **диск** — ``statvfs`` пустой директории, забиндленной с корневого раздела
  хоста (``./host-metrics:/host/fs:ro``): statvfs возвращает статистику ФС,
  содержимое каталога не читается;
- **сеть** — счётчики собственного veth (``/proc/net/dev`` в netns контейнера):
  трафик сервиса, а не всего хоста.

Схема сбора (см. docs/tz/server-monitoring.md):
- тик раз в 5 минут (APScheduler): сэмпл → Redis-буфер ``server_metrics:buffer``
  (LIST, TTL 12 ч — переживает один пропущенный flush);
- flush раз в 6 часов: сэмплы закрытых окон агрегируются в одну строку
  ``server_metrics`` (avg/max для CPU и load, last для диска/RAM, сумма дельт
  для сети), буфер обрезается;
- cleanup раз в сутки: строки старше ``settings.server_metrics_retention_days``.

CPU % и сетевые дельты считаются между двумя чтениями кумулятивных каунтеров
(``/proc/stat`` и ``/proc/net/dev``); предыдущие каунтеры хранятся в Redis
(переживают рестарт). Первый тик после рестарта записывает cpu/net = NULL.

Алерты (``evaluate_alerts``) вычисляются по последнему живому сэмплу из Redis
(свежесть ≤ 5 мин) с фолбэком на последнюю строку БД: critical — превышение
лимита, warning — достижение 80% лимита (раннее предупреждение о заполнении диска).
"""

import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
import sqlalchemy as sa
from opentelemetry import trace
from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import ServerMetric

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Окно агрегации в БД (одна строка на окно).
WINDOW_SECONDS = 6 * 60 * 60

# Redis-ключи.
BUFFER_KEY = "server_metrics:buffer"  # LIST JSON-сэмплов
STATE_KEY = "server_metrics:prev"  # HASH кумулятивных каунтеров CPU/сети
LAST_KEY = "server_metrics:last"  # последний сэмпл (live-алерты)

# TTL буфера — 12 ч (переживает один пропущенный flush).
BUFFER_TTL_SECONDS = 12 * 60 * 60
# TTL последнего сэмпла — если коллектор умер, «живой» алерт гаснет сам.
LAST_TTL_SECONDS = 30 * 60
# TTL каунтеров — чтобы при отключении фичи ключ не висел вечно.
STATE_TTL_SECONDS = 7 * 24 * 60 * 60

SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
# Warning при достижении этой доли лимита.
WARNING_RATIO = 0.8

_GB = 1024**3
_MB = 1000 * 1000


@dataclass(frozen=True)
class Alert:
    """Сработавший лимит метрики хоста (для плашки на /server и бейджей)."""

    metric: str
    value: float
    limit: float
    severity: str


@dataclass(frozen=True)
class HostSample:
    """Один 5-минутный сэмпл нагрузки хоста (NULL — источник недоступен).

    cpu/net — дельты кумулятивных каунтеров между тиками; сеть — счётчики veth
    контейнера (трафик сервиса, не всего хоста).
    """

    ts: datetime
    cpu_percent: float | None
    load1: float | None
    load5: float | None
    load15: float | None
    mem_used_percent: float | None
    disk_used_percent: float | None
    disk_free_gb: float | None
    net_rx_bytes: float | None
    net_tx_bytes: float | None


@dataclass(frozen=True)
class _TickResult:
    """Сэмпл тика + текущие кумулятивные каунтеры (для STATE_KEY)."""

    sample: HostSample
    cpu: tuple[int, int] | None
    net: tuple[int, int] | None


def _floor_to_window(dt: datetime) -> datetime:
    """Округляет ``dt`` вниз до начала 6-часового окна (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    seconds = int((dt - epoch).total_seconds())
    floored = seconds - (seconds % WINDOW_SECONDS)
    return epoch + timedelta(seconds=floored)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _last(values: list[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def aggregate_samples(samples: list[HostSample], window_start: datetime) -> dict[str, Any]:
    """Агрегирует сэмплы одного 6-часового окна в dict колонок ``ServerMetric`` (чистая).

    avg — для CPU и load average, max — для пиков CPU/load5, последнее наблюдение —
    для диска/RAM, сумма дельт — для сети. Сэмплы с NULL в метрике пропускаются.
    """
    cpu = [s.cpu_percent for s in samples if s.cpu_percent is not None]
    load1 = [s.load1 for s in samples if s.load1 is not None]
    load5 = [s.load5 for s in samples if s.load5 is not None]
    load15 = [s.load15 for s in samples if s.load15 is not None]
    rx = [s.net_rx_bytes for s in samples if s.net_rx_bytes is not None]
    tx = [s.net_tx_bytes for s in samples if s.net_tx_bytes is not None]
    return {
        "bucket_ts": window_start,
        "cpu_avg": _mean(cpu),
        "cpu_max": round(max(cpu), 2) if cpu else None,
        "load1_avg": _mean(load1),
        "load5_avg": _mean(load5),
        "load15_avg": _mean(load15),
        "load5_max": round(max(load5), 2) if load5 else None,
        "mem_used_percent": _last([s.mem_used_percent for s in samples]),
        "disk_used_percent": _last([s.disk_used_percent for s in samples]),
        "disk_free_gb": _last([s.disk_free_gb for s in samples]),
        "net_rx_mb": round(sum(rx) / _MB, 2) if rx else None,
        "net_tx_mb": round(sum(tx) / _MB, 2) if tx else None,
    }


def _first_attr(metric: Any, names: tuple[str, ...]) -> float | None:
    """Первый не-None атрибут из ``names`` (у HostSample и ServerMetric разные имена полей)."""
    for name in names:
        value = getattr(metric, name, None)
        if value is not None:
            return float(value)
    return None


def evaluate_alerts(metric: Any) -> list[Alert]:
    """Сравнивает метрики с лимитами из конфига (чистая, без побочных эффектов).

    Работает и с живым ``HostSample``, и со строкой ``ServerMetric``: CPU и load
    берутся по худшему наблюдению (``cpu_percent``/``cpu_max``, ``load5``/``load5_max``),
    диск и RAM — по последнему значению. ``None`` (нет данных) — проверка пропускается.
    """
    alerts: list[Alert] = []
    checks: list[tuple[str, float | None, float]] = [
        ("disk", _first_attr(metric, ("disk_used_percent",)), float(settings.server_alert_disk_percent)),
        ("cpu", _first_attr(metric, ("cpu_percent", "cpu_max")), float(settings.server_alert_cpu_percent)),
        ("mem", _first_attr(metric, ("mem_used_percent",)), float(settings.server_alert_mem_percent)),
        ("load5", _first_attr(metric, ("load5", "load5_max")), float(settings.server_alert_load5)),
    ]
    for name, value, limit in checks:
        if value is None:
            continue
        if value >= limit:
            alerts.append(Alert(metric=name, value=value, limit=limit, severity=SEVERITY_CRITICAL))
        elif value >= limit * WARNING_RATIO:
            alerts.append(Alert(metric=name, value=round(value, 2), limit=limit, severity=SEVERITY_WARNING))
    return alerts


# ----------------------------------------------------------------------
# Чтение /proc (собственный, контейнера) и statvfs (defensive: неудача → None)
# ----------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _read_cpu_counters(proc_dir: Path) -> tuple[int, int] | None:
    """``(idle, total)`` из первой строки /proc/stat (jiffies); idle включает iowait."""
    text = _read_text(proc_dir / "stat")
    if not text:
        return None
    first = text.splitlines()[0] if text.splitlines() else ""
    parts = first.split()[1:]
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def _read_meminfo(proc_dir: Path) -> float | None:
    """Занятая RAM в %: 100 × (1 − MemAvailable / MemTotal)."""
    text = _read_text(proc_dir / "meminfo")
    if text is None:
        return None
    info: dict[str, str] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        fields = rest.split()
        if fields:
            info[name.strip()] = fields[0]
    if "MemTotal" not in info or "MemAvailable" not in info:
        return None
    try:
        total = float(info["MemTotal"])
        available = float(info["MemAvailable"])
    except ValueError:
        return None
    if total <= 0:
        return None
    return round(100.0 * (1.0 - available / total), 2)


def _read_loadavg(proc_dir: Path) -> tuple[float, float, float] | None:
    """(1, 5, 15) из /proc/loadavg."""
    text = _read_text(proc_dir / "loadavg")
    if text is None:
        return None
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None


def _read_net_counters(proc_dir: Path) -> tuple[int, int] | None:
    """Суммарные (rx_bytes, tx_bytes) по всем интерфейсам, кроме loopback ``lo``.

    Читается собственный /proc/net/dev контейнера: в netns это счётчики veth,
    то есть трафик сервиса (не всего хоста).
    """
    text = _read_text(proc_dir / "net" / "dev")
    if text is None:
        return None
    rx = tx = 0
    for line in text.splitlines()[2:]:  # две строки заголовка
        iface, _, rest = line.partition(":")
        if iface.strip() == "lo":
            continue
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            rx += int(fields[0])
            tx += int(fields[8])
        except ValueError:
            continue
    return rx, tx


def _read_disk(disk_path: Path) -> tuple[float, float] | None:
    """``(used_percent, free_gb)`` по statvfs пути; free — доступное не-root (f_bavail).

    ``disk_path`` — пустая директория, забиндленная с мониторимого раздела хоста:
    statvfs возвращает статистику файловой системы, содержимое каталога не читается.
    """
    try:
        st = os.statvfs(disk_path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return None
    return round(100.0 * (1.0 - free / total), 2), round(free / _GB, 2)


def _cpu_percent(prev_idle: int, prev_total: int, idle: int, total: int) -> float | None:
    """Загрузка CPU между двумя чтениями /proc/stat; None при некорректной дельте."""
    d_total = total - prev_total
    d_idle = idle - prev_idle
    if d_total <= 0 or d_idle < 0 or d_idle > d_total:
        return None
    return round(min(100.0, max(0.0, 100.0 * (1.0 - d_idle / d_total))), 2)


def _net_delta(prev: int, current: int) -> float | None:
    """Дельта кумулятивного счётчика; None при сбросе (рестарт хоста)."""
    d = current - prev
    return float(d) if d >= 0 else None


def _cpu_from_prev(prev: dict[str, str], cpu: tuple[int, int]) -> float | None:
    """CPU % относительно каунтеров прошлого тика; None, если прошлых нет/битые."""
    try:
        return _cpu_percent(int(prev["cpu_idle"]), int(prev["cpu_total"]), cpu[0], cpu[1])
    except (KeyError, ValueError):
        return None


def _net_from_prev(prev: dict[str, str], net: tuple[int, int]) -> tuple[float | None, float | None]:
    """(rx, tx) дельты относительно каунтеров прошлого тика; None, если прошлых нет/битые."""
    try:
        return _net_delta(int(prev["net_rx"]), net[0]), _net_delta(int(prev["net_tx"]), net[1])
    except (KeyError, ValueError):
        return None, None


def _collect_sample(proc_dir: Path, disk_path: Path, prev: dict[str, str]) -> _TickResult | None:
    """Собирает сэмпл из всех источников; None, если не прочитался ни один."""
    cpu = _read_cpu_counters(proc_dir)
    net = _read_net_counters(proc_dir)
    load = _read_loadavg(proc_dir)
    mem = _read_meminfo(proc_dir)
    disk = _read_disk(disk_path)
    if cpu is None and net is None and load is None and mem is None and disk is None:
        return None

    cpu_percent = _cpu_from_prev(prev, cpu) if cpu is not None else None
    net_rx, net_tx = (None, None) if net is None else _net_from_prev(prev, net)
    sample = HostSample(
        ts=datetime.now(UTC),
        cpu_percent=cpu_percent,
        load1=load[0] if load else None,
        load5=load[1] if load else None,
        load15=load[2] if load else None,
        mem_used_percent=mem,
        disk_used_percent=disk[0] if disk else None,
        disk_free_gb=disk[1] if disk else None,
        net_rx_bytes=net_rx,
        net_tx_bytes=net_tx,
    )
    return _TickResult(sample=sample, cpu=cpu, net=net)


def _sample_to_json(sample: HostSample) -> str:
    data = asdict(sample)
    data["ts"] = sample.ts.isoformat()
    return json.dumps(data)


def _parse_sample(raw: str | bytes) -> HostSample | None:
    try:
        data = json.loads(raw)
        data["ts"] = datetime.fromisoformat(data["ts"])
        return HostSample(**data)
    except (ValueError, TypeError, KeyError):
        return None


def _decode_map(data: dict[Any, Any]) -> dict[str, str]:
    """Нормализует результат HGETALL к dict[str, str] независимо от decode_responses."""
    result: dict[str, str] = {}
    for key, value in data.items():
        k = key.decode() if isinstance(key, bytes) else str(key)
        v = value.decode() if isinstance(value, bytes) else str(value)
        result[k] = v
    return result


class ServerMetricsService:
    """Сбор метрик хоста: 5-минутные сэмплы в Redis → 6-часовые строки в БД.

    Джобы регистрируются в ``dependencies.py`` (tick / flush / cleanup). Все
    операции defensive: метрики нельзя дать уронить приложение — любой сбой
    чтения хоста или Redis логируется и пропускается.
    """

    def __init__(
        self,
        db_session_factory: Callable[[], AsyncSession],
        proc_dir: str | None = None,
        disk_path: str | None = None,
    ) -> None:
        self._db = db_session_factory
        self._r: Redis | None = None
        # Каталоги переопределяются тестами; продакшн-дефолты — из конфига.
        self._proc_dir = Path(proc_dir or settings.server_metrics_proc_dir)
        self._disk_path = Path(disk_path or settings.server_metrics_disk_path)

    async def startup(self, redis: aioredis.Redis) -> None:
        self._r = redis
        logger.info("ServerMetricsService started (proc=%s, disk=%s)", self._proc_dir, self._disk_path)

    # ------------------------------------------------------------------
    # Сбор (тик раз в 5 минут)
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        """Джоба APScheduler: снять сэмпл хоста и положить в Redis-буфер."""
        try:
            await self._tick()
        except Exception:
            logger.error("server metrics tick failed", exc_info=True)

    async def _tick(self) -> None:
        if self._r is None:
            return
        prev = _decode_map(await self._r.hgetall(STATE_KEY))
        result = _collect_sample(self._proc_dir, self._disk_path, prev)
        if result is None:
            logger.debug(
                "No host metrics readable (proc=%s, disk=%s), skipping tick", self._proc_dir, self._disk_path
            )
            return
        await self._store_sample(result)

    async def _store_sample(self, result: _TickResult) -> None:
        if self._r is None:
            return
        sample_json = _sample_to_json(result.sample)
        pipe = self._r.pipeline(transaction=False)
        pipe.rpush(BUFFER_KEY, sample_json)
        pipe.expire(BUFFER_KEY, BUFFER_TTL_SECONDS)
        pipe.set(LAST_KEY, sample_json, ex=LAST_TTL_SECONDS)
        mapping: dict[str, str] = {}
        if result.cpu is not None:
            mapping |= {"cpu_idle": str(result.cpu[0]), "cpu_total": str(result.cpu[1])}
        if result.net is not None:
            mapping |= {"net_rx": str(result.net[0]), "net_tx": str(result.net[1])}
        if mapping:
            pipe.hset(STATE_KEY, mapping=mapping)  # type: ignore[arg-type]  # как в statistics.set_gauge
            pipe.expire(STATE_KEY, STATE_TTL_SECONDS)
        await pipe.execute()

    # ------------------------------------------------------------------
    # Flush (раз в 6 часов) и cleanup (раз в сутки)
    # ------------------------------------------------------------------

    @tracer.start_as_current_span("ServerMetrics: flush to DB")
    async def flush_to_db(self) -> None:
        """Джоба APScheduler: агрегировать буфер закрытых окон в ``server_metrics``."""
        try:
            await self._flush()
        except Exception:
            logger.error("server metrics flush failed", exc_info=True)

    async def _flush(self) -> None:
        if self._r is None:
            return
        raw_items = await self._r.lrange(BUFFER_KEY, 0, -1)
        samples = [s for s in (_parse_sample(raw) for raw in raw_items) if s is not None]
        if not samples:
            return
        current_window = _floor_to_window(datetime.now(UTC))
        closed = [s for s in samples if s.ts < current_window]
        if not closed:
            return

        windows: dict[datetime, list[HostSample]] = {}
        for sample in closed:
            windows.setdefault(_floor_to_window(sample.ts), []).append(sample)
        rows = [aggregate_samples(group, window) for window, group in sorted(windows.items())]

        async with self._db() as session:
            await session.execute(
                pg_insert(ServerMetric).values(rows).on_conflict_do_nothing(index_elements=["bucket_ts"])
            )
            await session.commit()

        # Обработанные сэмплы убираем из буфера (окно закрыто, повторная
        # агрегация не нужна); сэмплы текущего окна остаются до следующего flush.
        keep = len(samples) - len(closed)
        if keep:
            await self._r.ltrim(BUFFER_KEY, -keep, -1)
        else:
            await self._r.delete(BUFFER_KEY)

    @tracer.start_as_current_span("ServerMetrics: retention cleanup")
    async def cleanup_old_data(self) -> None:
        """Джоба APScheduler: удалить строки старше ``server_metrics_retention_days``."""
        async with self._db() as session:
            cutoff = datetime.now(UTC) - timedelta(days=settings.server_metrics_retention_days)
            await session.execute(sa.delete(ServerMetric).where(ServerMetric.bucket_ts < cutoff))
            await session.commit()

    # ------------------------------------------------------------------
    # Чтение (API страницы «Сервер»)
    # ------------------------------------------------------------------

    async def get_chart(self, dt_from: datetime, dt_to: datetime) -> list[ServerMetric]:
        """Строки метрик за диапазон [dt_from, dt_to], по возрастанию окна."""
        async with self._db() as session:
            return list(
                (
                    await session.execute(
                        sa.select(ServerMetric)
                        .where(ServerMetric.bucket_ts >= dt_from, ServerMetric.bucket_ts <= dt_to)
                        .order_by(ServerMetric.bucket_ts)
                    )
                )
                .scalars()
                .all()
            )

    async def get_latest_row(self) -> ServerMetric | None:
        async with self._db() as session:
            return (
                (await session.execute(sa.select(ServerMetric).order_by(ServerMetric.bucket_ts.desc()).limit(1)))
                .scalars()
                .first()
            )

    async def get_active_alerts(self) -> list[Alert]:
        """Алерты по последнему живому сэмплу из Redis (свежесть ≤ 5 мин).

        Фолбэк — последняя строка БД (например, dev без /host-маунта, но с историей);
        если данных нет вовсе — пустой список.
        """
        if self._r is not None:
            raw = await self._r.get(LAST_KEY)
            if raw:
                sample = _parse_sample(raw)
                if sample is not None:
                    return evaluate_alerts(sample)
        latest = await self.get_latest_row()
        if latest is None:
            return []
        return evaluate_alerts(latest)
