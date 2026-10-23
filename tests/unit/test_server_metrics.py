# ruff: noqa: S101

"""Юнит-тесты коллектора метрик хоста (services/server_metrics.py)."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from config import settings
from database.models import ServerMetric
from services.server_metrics import (
    BUFFER_KEY,
    FALLBACK_MAX_AGE,
    LAST_KEY,
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    STATE_KEY,
    HostSample,
    ServerMetricsService,
    _cpu_percent,
    _floor_to_window,
    _sample_to_json,
    aggregate_samples,
    ensure_utc,
    evaluate_alerts,
)
from tests.unit.fixtures.fake_redis import FakeRedis, session_factory

# ----------------------------------------------------------------------
# Фейк Redis и хелперы (общие — tests/unit/fixtures/fake_redis.py)
# ----------------------------------------------------------------------


def _factory(session: Any):
    return session_factory(session)


def _sample(ts: datetime, **kwargs: Any) -> HostSample:
    defaults: dict[str, Any] = {
        "cpu_percent": 10.0,
        "load1": 0.5,
        "load5": 0.7,
        "load15": 0.9,
        "mem_used_percent": 55.0,
        "disk_used_percent": 60.0,
        "disk_free_gb": 40.0,
        "net_rx_bytes": 1_000_000.0,
        "net_tx_bytes": 500_000.0,
    }
    defaults.update(kwargs)
    return HostSample(ts=ts, **defaults)


# ----------------------------------------------------------------------
# evaluate_alerts
# ----------------------------------------------------------------------


def test_evaluate_alerts_ok_values_no_alerts():
    metric = SimpleNamespace(disk_used_percent=50.0, cpu_percent=40.0, mem_used_percent=60.0, load5=1.0)
    assert evaluate_alerts(metric) == []


def test_evaluate_alerts_critical_on_limit():
    metric = SimpleNamespace(disk_used_percent=90.0, cpu_percent=None, mem_used_percent=None, load5=None)
    alerts = evaluate_alerts(metric)
    assert [(a.metric, a.severity) for a in alerts] == [("disk", SEVERITY_CRITICAL)]


def test_evaluate_alerts_warning_band():
    # disk 90 → warning при ≥ 0.8×90 = 72
    metric = SimpleNamespace(disk_used_percent=75.0, cpu_percent=None, mem_used_percent=None, load5=None)
    alerts = evaluate_alerts(metric)
    assert [(a.metric, a.severity) for a in alerts] == [("disk", SEVERITY_WARNING)]
    assert alerts[0].limit == float(settings.monitoring.alert_disk_percent)


def test_evaluate_alerts_uses_cpu_max_for_db_row():
    row = SimpleNamespace(disk_used_percent=None, cpu_max=95.0, mem_used_percent=None, load5_max=None)
    alerts = evaluate_alerts(row)
    assert [(a.metric, a.severity) for a in alerts] == [("cpu", SEVERITY_CRITICAL)]


def test_evaluate_alerts_load5_thresholds():
    ok = SimpleNamespace(disk_used_percent=None, cpu_percent=None, mem_used_percent=None, load5=3.0)
    assert evaluate_alerts(ok) == []
    warning = SimpleNamespace(disk_used_percent=None, cpu_percent=None, mem_used_percent=None, load5=3.4)
    assert [a.severity for a in evaluate_alerts(warning)] == [SEVERITY_WARNING]
    critical = SimpleNamespace(disk_used_percent=None, cpu_percent=None, mem_used_percent=None, load5=4.5)
    assert [a.severity for a in evaluate_alerts(critical)] == [SEVERITY_CRITICAL]


def test_evaluate_alerts_all_metrics_and_none_safe():
    empty = SimpleNamespace()
    assert evaluate_alerts(empty) == []
    all_critical = SimpleNamespace(disk_used_percent=95.0, cpu_percent=92.0, mem_used_percent=93.0, load5=5.0)
    alerts = evaluate_alerts(all_critical)
    assert [a.metric for a in alerts] == ["disk", "cpu", "mem", "load5"]
    assert all(a.severity == SEVERITY_CRITICAL for a in alerts)


def test_evaluate_alerts_on_host_sample_boundary():
    # Чуть ниже warning-порога (0.8×90 = 72) — алертов нет.
    sample = _sample(datetime.now(UTC), cpu_percent=71.99, disk_used_percent=71.99)
    assert evaluate_alerts(sample) == []


# ----------------------------------------------------------------------
# aggregate_samples
# ----------------------------------------------------------------------


def test_aggregate_samples_avg_max_last_sum():
    window = _floor_to_window(datetime.now(UTC))
    samples = [
        _sample(
            window + timedelta(minutes=5),
            cpu_percent=10.0,
            load5=1.0,
            mem_used_percent=55.0,
            disk_used_percent=80.0,
            disk_free_gb=50.5,
            net_rx_bytes=1_000_000.0,
            net_tx_bytes=0.0,
        ),
        _sample(
            window + timedelta(minutes=10),
            cpu_percent=30.0,
            load5=3.0,
            mem_used_percent=None,
            disk_used_percent=82.0,
            disk_free_gb=49.0,
            net_rx_bytes=2_000_000.0,
            net_tx_bytes=1_000_000.0,
        ),
        _sample(
            window + timedelta(minutes=15),
            cpu_percent=None,
            load5=2.0,
            mem_used_percent=60.0,
            disk_used_percent=84.0,
            disk_free_gb=48.0,
            net_rx_bytes=None,
            net_tx_bytes=500_000.0,
        ),
    ]
    row = aggregate_samples(samples, window)
    assert row["bucket_ts"] == window
    assert row["cpu_avg"] == 20.0  # avg по не-None: (10+30)/2
    assert row["cpu_max"] == 30.0
    assert row["load5_avg"] == 2.0
    assert row["load5_max"] == 3.0
    assert row["mem_used_percent"] == 60.0  # последнее не-None
    assert row["disk_used_percent"] == 84.0
    assert row["disk_free_gb"] == 48.0
    assert row["net_rx_mb"] == 3.0  # (1M + 2M) / 1e6
    assert row["net_tx_mb"] == 1.5


def test_aggregate_samples_all_none_cpu():
    window = _floor_to_window(datetime.now(UTC))
    samples = [_sample(window, cpu_percent=None)]
    row = aggregate_samples(samples, window)
    assert row["cpu_avg"] is None
    assert row["cpu_max"] is None


def test_floor_to_window_is_six_hour_aligned():
    dt = datetime(2026, 10, 9, 13, 45, tzinfo=UTC)
    assert _floor_to_window(dt) == datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    naive = datetime(2026, 10, 9, 5, 0)
    assert _floor_to_window(naive) == datetime(2026, 10, 9, 0, 0, tzinfo=UTC)


def test_cpu_percent_delta():
    assert _cpu_percent(600, 800, 650, 900) == 50.0
    assert _cpu_percent(600, 800, 800, 1000) == 0.0  # d_idle == d_total → 0%
    assert _cpu_percent(700, 800, 600, 900) is None  # idle-каунтер скакнул — сброс
    assert _cpu_percent(800, 800, 800, 800) is None  # нулевая дельта total


# ----------------------------------------------------------------------
# Тик коллектора
# ----------------------------------------------------------------------


def _make_service(
    db_session: Any = None,
    proc_dir: str = "/nonexistent-proc",
    disk_path: str = "/nonexistent-disk",
) -> ServerMetricsService:
    return ServerMetricsService(
        _factory(db_session),
        proc_dir=proc_dir,
        disk_path=disk_path,
    )


@pytest.mark.asyncio
async def test_tick_skips_without_sources(tmp_path):
    service = _make_service(proc_dir=str(tmp_path / "no-proc"), disk_path=str(tmp_path / "no-disk"))
    fake = FakeRedis()
    await service.startup(fake)  # type: ignore[arg-type]
    await service.tick()
    assert fake.lists.get(BUFFER_KEY, []) == []
    assert fake.values.get(LAST_KEY) is None


def _write_host_files(root: Any) -> None:
    proc = root / "proc"
    (proc / "net").mkdir(parents=True, exist_ok=True)
    # cpu: idle=650, total=100+50+100+650=900; prev в STATE_KEY: 600/800 → 50%
    (proc / "stat").write_text("cpu  100 50 100 650 0 0 0 0\nctxt 1\nbtime 2\n")
    (proc / "meminfo").write_text("MemTotal:       1000 kB\nMemAvailable:    250 kB\n")
    (proc / "loadavg").write_text("0.50 0.70 0.90 1/100 12345\n")
    (proc / "net" / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets\n"
        "  eth0: 1000 0 0 0 0 0 0 0  500 0 0 0 0 0 0 0\n"
        "    lo: 999999 0 0 0 0 0 0 0  999999 0 0 0 0 0 0 0\n"
    )


@pytest.mark.asyncio
async def test_tick_records_sample_with_deltas(tmp_path):
    _write_host_files(tmp_path)
    service = _make_service(proc_dir=str(tmp_path / "proc"), disk_path=str(tmp_path))
    fake = FakeRedis()
    await fake.hset(STATE_KEY, mapping={"cpu_idle": 600, "cpu_total": 800, "net_rx": 0, "net_tx": 0})
    await service.startup(fake)  # type: ignore[arg-type]

    await service.tick()

    buffer = fake.lists[BUFFER_KEY]
    assert len(buffer) == 1
    sample = json.loads(buffer[0])
    assert sample["cpu_percent"] == 50.0  # d_idle=50 / d_total=100
    assert sample["mem_used_percent"] == 75.0
    assert sample["load1"] == 0.5
    assert sample["load5"] == 0.7
    assert sample["load15"] == 0.9
    assert sample["net_rx_bytes"] == 1000.0  # lo исключён
    assert sample["net_tx_bytes"] == 500.0
    assert sample["disk_used_percent"] is not None
    # Каунтеры обновились, последний сэмпл сохранён
    assert fake.hashes[STATE_KEY]["cpu_idle"] == "650"
    assert fake.values[LAST_KEY] == buffer[0]


@pytest.mark.asyncio
async def test_tick_first_run_without_prev_counters(tmp_path):
    """Первый тик после рестарта: cpu/net = NULL, но сэмпл записывается."""
    _write_host_files(tmp_path)
    service = _make_service(proc_dir=str(tmp_path / "proc"), disk_path=str(tmp_path))
    fake = FakeRedis()
    await service.startup(fake)  # type: ignore[arg-type]

    await service.tick()

    sample = json.loads(fake.lists[BUFFER_KEY][0])
    assert sample["cpu_percent"] is None
    assert sample["net_rx_bytes"] is None
    assert sample["mem_used_percent"] == 75.0


# ----------------------------------------------------------------------
# Flush / cleanup / чтение (с реальной БД из фикстур)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_aggregates_closed_windows_only(db_session):
    service = _make_service(db_session)
    fake = FakeRedis()
    await service.startup(fake)  # type: ignore[arg-type]

    now = datetime.now(UTC)
    prev_window = _floor_to_window(now) - timedelta(hours=6)
    closed_samples = [
        _sample(prev_window + timedelta(minutes=5), cpu_percent=10.0),
        _sample(prev_window + timedelta(minutes=10), cpu_percent=30.0),
    ]
    current_sample = _sample(now)  # текущее окно — не сбрасывается

    await fake.rpush(BUFFER_KEY, *[_sample_to_json(s) for s in [*closed_samples, current_sample]])
    await service.flush_to_db()

    rows = (await db_session.execute(sa.select(ServerMetric).order_by(ServerMetric.bucket_ts))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.bucket_ts == prev_window
    assert row.cpu_avg == 20.0
    assert row.cpu_max == 30.0
    # В буфере остался только сэмпл текущего окна
    assert len(fake.lists[BUFFER_KEY]) == 1


@pytest.mark.asyncio
async def test_flush_idempotent_on_conflict(db_session):
    service = _make_service(db_session)
    fake = FakeRedis()
    await service.startup(fake)  # type: ignore[arg-type]

    now = datetime.now(UTC)
    prev_window = _floor_to_window(now) - timedelta(hours=6)
    # Первый flush
    await fake.rpush(BUFFER_KEY, _sample_to_json(_sample(prev_window + timedelta(minutes=5), cpu_percent=10.0)))
    await service.flush_to_db()
    # Имитация сбоя после commit: буфер не очистился, flush повторился
    await fake.rpush(BUFFER_KEY, _sample_to_json(_sample(prev_window + timedelta(minutes=10), cpu_percent=50.0)))
    await service.flush_to_db()

    rows = (await db_session.execute(sa.select(ServerMetric))).scalars().all()
    assert len(rows) == 1  # ON CONFLICT DO NOTHING, дублей нет


@pytest.mark.asyncio
async def test_cleanup_old_data_retention(db_session):
    service = _make_service(db_session)
    old = ServerMetric(bucket_ts=datetime.now(UTC) - timedelta(days=settings.monitoring.metrics_retention_days + 5))
    fresh = ServerMetric(bucket_ts=datetime.now(UTC))
    db_session.add_all([old, fresh])
    await db_session.commit()

    await service.cleanup_old_data()

    remaining = (await db_session.execute(sa.select(ServerMetric))).scalars().all()
    assert [r.bucket_ts for r in remaining] == [fresh.bucket_ts]


@pytest.mark.asyncio
async def test_get_active_alerts_live_sample_wins_over_db(db_session):
    service = _make_service(db_session)
    fake = FakeRedis()
    await service.startup(fake)  # type: ignore[arg-type]

    # В БД — спокойная строка, в Redis — живой сэмпл с критичным диском
    db_session.add(ServerMetric(bucket_ts=_floor_to_window(datetime.now(UTC)), disk_used_percent=50.0))
    await db_session.commit()
    await fake.set(LAST_KEY, _sample_to_json(_sample(datetime.now(UTC), disk_used_percent=95.0)))

    alerts = await service.get_active_alerts()
    assert [(a.metric, a.severity) for a in alerts] == [("disk", SEVERITY_CRITICAL)]


@pytest.mark.asyncio
async def test_get_active_alerts_fallback_to_db(db_session):
    service = _make_service(db_session)
    await service.startup(FakeRedis())  # type: ignore[arg-type]

    db_session.add(ServerMetric(bucket_ts=_floor_to_window(datetime.now(UTC)), disk_used_percent=93.0))
    await db_session.commit()

    alerts = await service.get_active_alerts()
    assert [(a.metric, a.severity) for a in alerts] == [("disk", SEVERITY_CRITICAL)]


@pytest.mark.asyncio
async def test_get_active_alerts_no_data_returns_empty(db_session):
    service = _make_service(db_session)
    await service.startup(FakeRedis())  # type: ignore[arg-type]
    assert await service.get_active_alerts() == []


@pytest.mark.asyncio
async def test_get_active_alerts_stale_db_row_ignored(db_session):
    """Фолбэк на БД ограничен свежестью: строка старше FALLBACK_MAX_AGE не даёт алерт."""
    service = _make_service(db_session)
    await service.startup(FakeRedis())  # type: ignore[arg-type]

    stale_window = _floor_to_window(datetime.now(UTC) - FALLBACK_MAX_AGE - timedelta(hours=1))
    db_session.add(ServerMetric(bucket_ts=stale_window, disk_used_percent=95.0))
    await db_session.commit()

    assert await service.get_active_alerts() == []


# ----------------------------------------------------------------------
# Разное (нормализация дат, сбросы каунтеров, битые /proc-файлы)
# ----------------------------------------------------------------------


def test_ensure_utc_naive_treated_as_utc():
    naive = datetime(2026, 10, 10, 12, 0)
    assert ensure_utc(naive).tzinfo is UTC
    assert ensure_utc(naive) == datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    aware = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert ensure_utc(aware) == aware


def test_net_delta_counter_reset_returns_none():
    from services.server_metrics import _net_delta

    assert _net_delta(100, 150) == 50.0
    assert _net_delta(200, 150) is None  # сброс каунтера (рестарт хоста)


@pytest.mark.asyncio
async def test_tick_with_malformed_proc_files(tmp_path):
    """Битые /proc-файлы не роняют тик: поля None, остальные источники работают."""
    proc = tmp_path / "proc"
    proc.mkdir(parents=True)
    (proc / "stat").write_text("garbage without numbers\n")
    (proc / "meminfo").write_text("MemTotal: abc kB\n")
    (proc / "loadavg").write_text("")
    # net/dev отсутствует вовсе
    service = _make_service(proc_dir=str(proc), disk_path=str(tmp_path))
    fake = FakeRedis()
    await service.startup(fake)  # type: ignore[arg-type]

    await service.tick()

    sample = json.loads(fake.lists[BUFFER_KEY][0])
    assert sample["cpu_percent"] is None
    assert sample["mem_used_percent"] is None
    assert sample["load1"] is None
    assert sample["net_rx_bytes"] is None
    assert sample["disk_used_percent"] is not None  # statvfs tmp_path работает
