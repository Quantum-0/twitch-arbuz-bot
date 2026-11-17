# ruff: noqa: S101

"""Юнит-тесты опросщика серверной статистики PostgreSQL (services/pg_stats.py).

Семантика дельт кумулятивных счётчиков pg_stat_database: первый сэмпл — только
база для будущих дельт; рост → дельты в статистику; уменьшение (рестарт PG /
stats_reset) → дельты пропускаются, снапшот перезаписывается. Регистрация
джобы проверяется в test_lifespan.py (EXPECTED_JOBS).
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from services.pg_stats import PG_IO_QUERY, PREV_KEY, PREV_TTL_SECONDS, poll_pg_io
from tests.unit.fixtures.fake_redis import FakeRedis, SessionCtx


class _FakeResult:
    def __init__(self, row: dict[str, int] | None) -> None:
        self._row = row

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> dict[str, int] | None:
        return self._row


class _FakeSession:
    """Сессия, отдающая заготовленные строки pg_stat_database по очереди."""

    def __init__(self, rows: list[dict[str, int] | None]) -> None:
        self._rows = list(rows)

    async def execute(self, query: Any) -> _FakeResult:
        assert query is PG_IO_QUERY
        return _FakeResult(self._rows.pop(0))


def _row(**overrides: int) -> dict[str, int]:
    base = {
        "blks_read": 1000,
        "blks_hit": 5000,
        "tup_returned": 100,
        "tup_fetched": 200,
        "tup_inserted": 10,
        "tup_updated": 20,
        "tup_deleted": 5,
        "xact_commit": 300,
        "xact_rollback": 3,
    }
    base.update(overrides)
    return base


def _make(rows: list[dict[str, int] | None]) -> tuple[SimpleNamespace, FakeRedis, _FakeSession]:
    """Фейковый statistics (запись flush_live_delta) + FakeRedis + сессия."""
    calls: list[tuple[str, str, int]] = []

    async def fake_flush(type_: str, subtype: str, amount: int) -> None:
        calls.append((str(type_), subtype, amount))

    stats = SimpleNamespace(flush_live_delta=fake_flush, calls=calls)
    session = _FakeSession(rows)

    def factory() -> SessionCtx:
        return SessionCtx(session)

    return stats, FakeRedis(), factory


class TestPollPgIo:
    def test_query_filters_current_database(self):
        # Инвариант изоляции: без фильтра по текущей базе метрика начнёт
        # учитывать соседние базы кластера и строку _shared_ (PG 15+).
        assert "WHERE datname = current_database()" in str(PG_IO_QUERY)

    async def test_first_poll_stores_snapshot_without_deltas(self):
        stats, redis, factory = _make([_row()])
        await poll_pg_io(stats, factory, redis)
        assert stats.calls == []
        assert json.loads(redis.values[PREV_KEY]) == _row()
        assert redis.ttls[PREV_KEY] == PREV_TTL_SECONDS

    async def test_delta_written_on_growth(self):
        stats, redis, factory = _make(
            [
                _row(blks_read=1000, blks_hit=5000, tup_inserted=10, xact_rollback=3),
                _row(blks_read=1007, blks_hit=5040, tup_inserted=10, xact_rollback=9),
            ]
        )
        await poll_pg_io(stats, factory, redis)
        await poll_pg_io(stats, factory, redis)
        assert sorted(stats.calls) == [
            ("pg_io", "blks_hit", 40),
            ("pg_io", "blks_read", 7),
            ("pg_io", "xact_rollback", 6),
        ]
        # снапшот перезаписан свежим
        assert json.loads(redis.values[PREV_KEY])["blks_read"] == 1007

    async def test_restart_counters_decrease_skips_deltas(self):
        stats, redis, factory = _make([_row(blks_read=1000), _row(blks_read=5)])
        await poll_pg_io(stats, factory, redis)
        await poll_pg_io(stats, factory, redis)
        assert stats.calls == []
        # снапшот перезаписан меньшим — следующая дельта посчитается от него
        assert json.loads(redis.values[PREV_KEY])["blks_read"] == 5

    async def test_zero_deltas_not_written(self):
        stats, redis, factory = _make([_row(), _row()])
        await poll_pg_io(stats, factory, redis)
        await poll_pg_io(stats, factory, redis)
        assert stats.calls == []

    async def test_corrupted_prev_snapshot_skipped(self):
        stats, redis, factory = _make([_row(blks_read=1010)])
        redis.values[PREV_KEY] = "{not-json"
        await poll_pg_io(stats, factory, redis)
        assert stats.calls == []
        # битый снапшот заменён валидным — следующая дельта посчитается корректно
        assert json.loads(redis.values[PREV_KEY])["blks_read"] == 1010

    async def test_no_row_no_crash(self):
        stats, redis, factory = _make([None])
        await poll_pg_io(stats, factory, redis)
        assert stats.calls == []
        assert PREV_KEY not in redis.values

    async def test_null_counters_skip_sample(self):
        # NULL бывает при track_counts=off: сэмпл пропускается целиком,
        # прежний снапшот не трогается.
        row = _row()
        row["xact_commit"] = None  # type: ignore[assignment]
        stats, redis, factory = _make([_row(blks_read=1000), row])
        await poll_pg_io(stats, factory, redis)
        await poll_pg_io(stats, factory, redis)
        assert stats.calls == []
        assert json.loads(redis.values[PREV_KEY])["blks_read"] == 1000

    async def test_redis_set_failure_writes_no_deltas(self):
        # Фиксирует порядок «снапшот ДО дельт»: сбой записи снапшота не
        # оставляет записанных дельт — интервал не задаивается. Исключение
        # всплывает наружу (ловит обёртка джобы в dependencies.py).
        stats, redis, factory = _make([_row(blks_read=1000), _row(blks_read=1010)])
        await poll_pg_io(stats, factory, redis)

        async def failing_set(name: str, value: str, ex: int | None = None) -> None:  # noqa: ARG001
            raise ConnectionError("redis down")

        redis.set = failing_set  # type: ignore[method-assign]
        with pytest.raises(ConnectionError):
            await poll_pg_io(stats, factory, redis)
        assert stats.calls == []
