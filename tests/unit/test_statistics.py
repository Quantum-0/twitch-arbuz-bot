# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты подсистемы статистики: инкременты Redis, flush в БД (ON CONFLICT),
чтение графиков (counter/timing/gauge/series/users_count) и устойчивость к сбоям."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from redis.exceptions import RedisError

from database.models import Statistics, User
from schemas.api import StatsPeriod, StatsType
from services.statistics import (
    BUCKET_SECONDS,
    RETENTION_DAYS,
    SUM_MS_SUFFIX,
    StatisticsService,
    _field_for,
    _floor_to_bucket,
    _needs_empty_subtype_filter,
    _parse_field,
    _split_allowed_subtypes,
)
from tests.unit.fixtures.fake_redis import FakeRedis, SessionCtx
from utils.enums import SSEChannel

# staticmethod-функции класса, используемые напрямую
_compute_bucket_range = StatisticsService._compute_bucket_range
_normalize_range = StatisticsService._normalize_range

OLD_BUCKET = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
OLD_KEY = "statistics:bucket:2026-01-01T00:00:00"


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def service(db_session, fake) -> StatisticsService:
    return StatisticsService(lambda: SessionCtx(db_session), fake)


def make_service(db_session, redis) -> StatisticsService:
    return StatisticsService(lambda: SessionCtx(db_session), redis)


async def fetch_stat(db_session, bucket: datetime, type_: str, subtype: str = "") -> Statistics | None:
    result = await db_session.execute(
        sa.select(Statistics).where(
            Statistics.bucket_ts == bucket,
            Statistics.type == type_,
            Statistics.subtype == subtype,
            Statistics.channel_id.is_(None),
        )
    )
    return result.scalar_one_or_none()


class TestHelpers:
    def test_floor_to_bucket_utc(self):
        dt = datetime(2026, 10, 25, 12, 17, 43, tzinfo=UTC)
        assert _floor_to_bucket(dt, 600) == datetime(2026, 10, 25, 12, 10, tzinfo=UTC)

    def test_floor_to_bucket_naive_gets_utc(self):
        dt = datetime(2026, 10, 25, 12, 17, 43)
        floored = _floor_to_bucket(dt, 600)
        assert floored.tzinfo is UTC
        assert floored.minute == 10

    def test_floor_to_bucket_aligned_kept(self):
        dt = datetime(2026, 10, 25, 12, 10, 0, tzinfo=UTC)
        assert _floor_to_bucket(dt, 600) == dt

    def test_field_for_roundtrip(self):
        field = _field_for("cmd", "dice", None)
        assert field == "cmd:dice:"
        assert _parse_field(field) == ("cmd", "dice", None)

        field2 = _field_for("msg", "", 42)
        assert field2 == "msg::42"
        assert _parse_field(field2) == ("msg", "", 42)

    def test_parse_field_bad_channel(self):
        assert _parse_field("msg::abc") == ("msg", "", None)

    def test_needs_empty_subtype_filter(self):
        assert _needs_empty_subtype_filter("message_processing_time") is True
        assert _needs_empty_subtype_filter("heat_proxy_bytes") is True
        # ai_sticker_processing_time использует осмысленные подтипы
        assert _needs_empty_subtype_filter("ai_sticker_processing_time") is False
        assert _needs_empty_subtype_filter("message_incoming") is False

    def test_split_allowed_subtypes(self):
        assert _split_allowed_subtypes("sse_connections") == {ch.value for ch in SSEChannel}
        assert _split_allowed_subtypes("command_handled") is None

    def test_normalize_range_defaults(self):
        to, frm = _normalize_range(None, None, timedelta(hours=1))
        now = datetime.now(UTC)
        assert abs((to - now).total_seconds()) < 5
        assert (to - frm) == timedelta(hours=1)

    def test_normalize_range_clamps_window(self):
        to = datetime(2026, 10, 25, tzinfo=UTC)
        frm = datetime(2020, 1, 1, tzinfo=UTC)
        to2, frm2 = _normalize_range(frm, to, timedelta(days=1))
        assert to2 == to
        assert frm2 == to - timedelta(days=1)

    def test_normalize_range_naive_treated_as_utc(self):
        to, frm = _normalize_range(None, datetime(2026, 10, 25, 12, 0), timedelta(hours=1))
        assert to.tzinfo is UTC
        assert frm == datetime(2026, 10, 25, 11, 0, tzinfo=UTC)

    def test_compute_bucket_range_empty_range(self):
        dt = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
        assert _compute_bucket_range(dt, dt, timedelta(days=1), 600) is None
        assert _compute_bucket_range(dt + timedelta(hours=1), dt, timedelta(days=1), 600) is None

    def test_compute_bucket_range_cuts_incomplete(self):
        # now=12:07 → текущий бакет 12:00; запрошенный to=12:20 → end=12:00
        now_bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
        rng = _compute_bucket_range(
            now_bucket - timedelta(hours=1), now_bucket + timedelta(minutes=30), timedelta(days=1), 600
        )
        assert rng is not None
        assert rng[1] <= now_bucket


class TestBuildRowsFromHash:
    def test_counter_only(self):
        rows = StatisticsService._build_rows_from_hash({"message_incoming::": "5"}, OLD_BUCKET)
        assert rows == [
            {"bucket_ts": OLD_BUCKET, "type": "message_incoming", "subtype": "", "channel_id": None, "count": 5}
        ]

    def test_timing_joins_sum_ms(self):
        raw = {
            "message_processing_time::": "2",
            f"message_processing_time{SUM_MS_SUFFIX}::": "300",
        }
        rows = StatisticsService._build_rows_from_hash(raw, OLD_BUCKET)
        assert rows == [
            {
                "bucket_ts": OLD_BUCKET,
                "type": "message_processing_time",
                "subtype": "",
                "channel_id": None,
                "count": 2,
                "sum_ms": 300,
            }
        ]

    def test_orphan_sum_ms_count_zero(self):
        rows = StatisticsService._build_rows_from_hash({f"heat_proxy_bytes{SUM_MS_SUFFIX}::": "123"}, OLD_BUCKET)
        assert rows == [
            {
                "bucket_ts": OLD_BUCKET,
                "type": "heat_proxy_bytes",
                "subtype": "",
                "channel_id": None,
                "count": 0,
                "sum_ms": 123,
            }
        ]

    def test_non_int_value_skipped(self):
        assert StatisticsService._build_rows_from_hash({"a::": "oops"}, OLD_BUCKET) == []

    def test_bytes_decoded(self):
        rows = StatisticsService._build_rows_from_hash({b"cmd:dice:": b"7"}, OLD_BUCKET)
        assert rows[0]["type"] == "cmd"
        assert rows[0]["subtype"] == "dice"
        assert rows[0]["count"] == 7


class TestRedisAccumulation:
    async def test_inc_writes_bucket_hash(self, service, fake):
        service.inc("message_incoming")
        await asyncio.sleep(0)
        key = service._bucket_key(_floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS))
        assert fake.hashes.get(key) == {"message_incoming::": "1"}
        assert fake.ttls.get(key) == StatisticsService.HASH_TTL_SECONDS

    async def test_inc_accumulates(self, service, fake):
        service.inc("message_incoming", amount=4)
        service.inc("message_incoming", amount=2)
        await asyncio.sleep(0)
        key = next(k for k in fake.hashes if k.startswith(StatisticsService.HASH_KEY_PREFIX))
        assert fake.hashes[key]["message_incoming::"] == "6"

    async def test_inc_with_subtype_and_channel(self, service, fake):
        service.inc("command_handled", subtype="dice", channel_id=99)
        await asyncio.sleep(0)
        key = next(k for k in fake.hashes if k.startswith(StatisticsService.HASH_KEY_PREFIX))
        assert fake.hashes[key]["command_handled:dice:99"] == "1"

    async def test_inc_timing_two_fields(self, service, fake):
        service.inc_timing("message_processing_time", value_ms=250)
        service.inc_timing("message_processing_time", value_ms=50)
        await asyncio.sleep(0)
        key = next(k for k in fake.hashes if k.startswith(StatisticsService.HASH_KEY_PREFIX))
        assert fake.hashes[key]["message_processing_time::"] == "2"
        assert fake.hashes[key][f"message_processing_time{SUM_MS_SUFFIX}::"] == "300"

    async def test_set_gauge_overwrites(self, service, fake):
        service.set_gauge("sse_connections", {"total": 3})
        await asyncio.sleep(0)
        key = next(k for k in fake.hashes if k.startswith(StatisticsService.HASH_KEY_PREFIX))
        assert fake.hashes[key]["sse_connections:total:"] == "3"

        # Gauge — не инкремент: повторная запись перезаписывает
        service.set_gauge("sse_connections", {"total": 7})
        await asyncio.sleep(0)
        assert fake.hashes[key]["sse_connections:total:"] == "7"

    async def test_set_gauge_zero_subtype_removed(self, service, fake):
        key = service._bucket_key(_floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS))
        fake._hset(key, {"sse_connections:heat:": "5"})

        service.set_gauge("sse_connections", {"total": 2, "heat": 0})
        await asyncio.sleep(0)

        assert fake.hashes[key] == {"sse_connections:total:": "2"}

    async def test_set_gauge_empty_values_noop(self, service, fake):
        service.set_gauge("sse_connections", {})
        await asyncio.sleep(0)
        assert not any(k.startswith(StatisticsService.HASH_KEY_PREFIX) for k in fake.hashes)
        assert all(t.done() for t in service._tasks)

    async def test_mark_channel_sadd(self, service, fake):
        service.mark_channel("incoming", 42)
        service.mark_channel("incoming", 42)
        service.mark_channel("incoming", 7)
        await asyncio.sleep(0)
        key = next(k for k in fake.sets if k.startswith(StatisticsService.CHANNELS_KEY_PREFIX))
        assert key.startswith("statistics:channels:incoming:")
        assert fake.sets[key] == {"42", "7"}
        assert fake.ttls.get(key) == StatisticsService.HASH_TTL_SECONDS

    async def test_redis_outage_does_not_raise(self, db_session):
        class BrokenRedis(FakeRedis):
            def pipeline(self, transaction: bool = False) -> Any:  # noqa: ARG002
                raise RedisError("down")

        service = make_service(db_session, BrokenRedis())
        service.inc("message_incoming")
        service.inc_timing("message_processing_time", value_ms=5)
        service.mark_channel("incoming", 1)
        service.set_gauge("sse_connections", {"total": 1})
        for task in list(service._tasks):
            await task


class TestFlushToDb:
    async def test_counter_dumped_and_key_deleted(self, db_session, fake, service):
        fake._hset(OLD_KEY, {"message_incoming::": "5", "command_handled:dice:": "2"})

        await service.flush_to_db()

        row = await fetch_stat(db_session, OLD_BUCKET, "message_incoming")
        assert row is not None
        assert row.count == 5
        cmd_row = await fetch_stat(db_session, OLD_BUCKET, "command_handled", "dice")
        assert cmd_row is not None
        assert cmd_row.count == 2
        assert OLD_KEY not in fake.hashes

    async def test_counter_upsert_sums(self, db_session, fake, service):
        db_session.add(Statistics(bucket_ts=OLD_BUCKET, type="message_incoming", subtype="", channel_id=None, count=10))
        await db_session.commit()
        fake._hset(OLD_KEY, {"message_incoming::": "5"})

        await service.flush_to_db()

        row = await fetch_stat(db_session, OLD_BUCKET, "message_incoming")
        assert row.count == 15  # 10 (было) + 5 (flush)

    async def test_timing_dumped_with_sum_ms(self, db_session, fake, service):
        fake._hset(OLD_KEY, {"message_processing_time::": "3", f"message_processing_time{SUM_MS_SUFFIX}::": "410"})

        await service.flush_to_db()

        row = await fetch_stat(db_session, OLD_BUCKET, "message_processing_time")
        assert row.count == 3
        assert row.sum_ms == 410

    async def test_gauge_overwrites_on_conflict(self, db_session, fake, service):
        db_session.add(
            Statistics(bucket_ts=OLD_BUCKET, type="sse_connections", subtype="total", channel_id=None, count=2)
        )
        await db_session.commit()
        fake._hset(OLD_KEY, {"sse_connections:total:": "9"})

        await service.flush_to_db()

        row = await fetch_stat(db_session, OLD_BUCKET, "sse_connections", "total")
        assert row.count == 9  # перезапись, не сумма

    async def test_current_bucket_skipped(self, db_session, fake, service):
        current_key = service._bucket_key(_floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS))
        fake._hset(current_key, {"message_incoming::": "1"})

        await service.flush_to_db()

        assert current_key in fake.hashes  # не залит и не удалён

    async def test_malformed_keys_skipped(self, db_session, fake, service):
        fake._hset("statistics:bucket:not-a-date", {"message_incoming::": "1"})
        fake.sets["statistics:channels:incoming:oops"] = {"1"}
        fake.sets["statistics:channels:nocolon"] = {"1"}

        await service.flush_to_db()  # не падает

        assert (await db_session.execute(sa.select(sa.func.count(Statistics.id)))).scalar_one() == 0
        assert "statistics:channels:nocolon" in fake.sets  # не распарсен — не тронут

    async def test_channels_set_dumped(self, db_session, fake, service):
        fake.sets["statistics:channels:incoming:2026-01-01T00:00:00"] = {"11", "22"}
        fake.sets["statistics:channels:outgoing:2026-01-01T00:00:00"] = {"11"}

        await service.flush_to_db()

        inc_row = await fetch_stat(db_session, OLD_BUCKET, "active_channels", "incoming")
        assert inc_row is not None
        assert inc_row.count == 2
        out_row = await fetch_stat(db_session, OLD_BUCKET, "active_channels", "outgoing")
        assert out_row is not None
        assert out_row.count == 1
        assert not any(k.startswith("statistics:channels:") for k in fake.sets)

    async def test_empty_channels_set_deleted_without_row(self, db_session, fake, service):
        fake.sets["statistics:channels:incoming:2026-01-01T00:00:00"] = set()

        await service.flush_to_db()

        assert not any(k.startswith("statistics:channels:") for k in fake.sets)
        row = await fetch_stat(db_session, OLD_BUCKET, "active_channels", "incoming")
        assert row is None

    async def test_current_channels_set_skipped(self, db_session, fake, service):
        current_key = service._channels_key("incoming", _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS))
        fake.sets[current_key] = {"5"}

        await service.flush_to_db()

        assert current_key in fake.sets

    async def test_scan_failure_graceful(self, db_session, fake, service, monkeypatch):
        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise RedisError("down")
            yield  # pragma: no cover

        monkeypatch.setattr(fake, "scan_iter", boom)
        await service.flush_to_db()  # не падает

    async def test_db_failure_keeps_redis_key(self, db_session, fake, service):
        fake._hset(OLD_KEY, {"message_incoming::": "5"})

        class FailingCtx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args: Any) -> bool:
                return False

            async def execute(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("db down")

            async def commit(self) -> None:
                return None

        service._db = lambda: FailingCtx()  # type: ignore[assignment]
        await service.flush_to_db()

        # Данные не потеряны: ключ остался в Redis
        assert OLD_KEY in fake.hashes


class TestCleanup:
    async def test_deletes_only_old_rows(self, db_session, fake, service):
        old_bucket = datetime.now(UTC) - timedelta(days=RETENTION_DAYS + 10)
        fresh_bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS) - timedelta(hours=1)
        db_session.add_all(
            [
                Statistics(bucket_ts=old_bucket, type="message_incoming", subtype="", channel_id=None, count=1),
                Statistics(bucket_ts=fresh_bucket, type="message_incoming", subtype="", channel_id=None, count=2),
            ]
        )
        await db_session.commit()

        await service.cleanup_old_data()

        assert await fetch_stat(db_session, old_bucket, "message_incoming") is None
        assert await fetch_stat(db_session, fresh_bucket, "message_incoming") is not None

    async def test_db_failure_no_raise(self, db_session, fake, service):
        class FailingCtx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args: Any) -> bool:
                return False

            async def execute(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("db down")

            async def commit(self) -> None:
                return None

        service._db = lambda: FailingCtx()  # type: ignore[assignment]
        await service.cleanup_old_data()  # не падает


class TestGetChart:
    @staticmethod
    async def seed(db_session, rows: list[tuple[datetime, str, str, int, int | None]]) -> None:
        db_session.add_all(
            Statistics(bucket_ts=b, type=t, subtype=s, channel_id=None, count=c, sum_ms=ms) for b, t, s, c, ms in rows
        )
        await db_session.commit()

    async def test_counter_sum_with_zero_fill(self, db_session, fake, service):
        base = _floor_to_bucket(datetime.now(UTC) - timedelta(hours=2), 600)
        await self.seed(
            db_session,
            [
                (base, "message_incoming", "", 3, None),
                (base + timedelta(seconds=600), "message_incoming", "", 4, None),
            ],
        )

        points = await service.get_chart(
            "message_incoming", period=StatsPeriod.TEN_MIN, dt_from=base, dt_to=base + timedelta(seconds=1800)
        )

        assert points == [
            (base, 3),
            (base + timedelta(seconds=600), 4),
            (base + timedelta(seconds=1200), 0),  # zero-fill
        ]

    async def test_timing_avg(self, db_session, fake, service):
        base = _floor_to_bucket(datetime.now(UTC) - timedelta(hours=2), 600)
        await self.seed(
            db_session,
            [
                (base, "message_processing_time", "", 2, 300),
                (base + timedelta(seconds=600), "message_processing_time", "", 1, 150),
            ],
        )

        points = await service.get_chart(
            "message_processing_time", period=StatsPeriod.TEN_MIN, dt_from=base, dt_to=base + timedelta(seconds=1800)
        )

        assert [v for _, v in points] == [150, 150, 0]

    async def test_gauge_avg_across_bucket(self, db_session, fake, service):
        # Две 10-минутные записи внутри одного часового бакета
        base = _floor_to_bucket(datetime.now(UTC) - timedelta(hours=2), 3600)
        await self.seed(
            db_session,
            [
                (base, "sse_connections", "total", 5, None),
                (base + timedelta(seconds=600), "sse_connections", "total", 15, None),
            ],
        )

        points = await service.get_chart(
            StatsType.SSE_CONNECTIONS,
            subtype="total",
            period=StatsPeriod.ONE_HOUR,
            dt_from=base,
            dt_to=base + timedelta(seconds=3600),
        )

        assert points == [(base, 10)]

    async def test_subtype_filter(self, db_session, fake, service):
        base = _floor_to_bucket(datetime.now(UTC) - timedelta(hours=2), 600)
        await self.seed(
            db_session,
            [
                (base, "command_handled", "dice", 5, None),
                (base, "command_handled", "ban", 100, None),
            ],
        )

        points = await service.get_chart(
            "command_handled",
            subtype="dice",
            period=StatsPeriod.TEN_MIN,
            dt_from=base,
            dt_to=base + timedelta(seconds=600),
        )
        assert points == [(base, 5)]

    async def test_empty_range(self, db_session, fake, service):
        dt = datetime.now(UTC)
        assert await service.get_chart("message_incoming", dt_from=dt, dt_to=dt) == []


class TestGetChartSeries:
    async def test_top_n_series(self, db_session, fake, service):
        base = _floor_to_bucket(datetime.now(UTC) - timedelta(hours=2), 600)
        db_session.add_all(
            [
                Statistics(bucket_ts=base, type="command_handled", subtype="a", channel_id=None, count=10),
                Statistics(bucket_ts=base, type="command_handled", subtype="b", channel_id=None, count=7),
                Statistics(bucket_ts=base, type="command_handled", subtype="c", channel_id=None, count=1),
            ]
        )
        await db_session.commit()

        series = await service.get_chart_series(
            "command_handled", period=StatsPeriod.TEN_MIN, dt_from=base, dt_to=base + timedelta(seconds=600), top_n=2
        )

        assert [name for name, _ in series] == ["a", "b"]
        assert series[0][1] == [(base, 10)]
        assert series[1][1] == [(base, 7)]

    async def test_sse_connections_only_per_channel(self, db_session, fake, service):
        base = _floor_to_bucket(datetime.now(UTC) - timedelta(hours=2), 600)
        db_session.add_all(
            [
                Statistics(bucket_ts=base, type="sse_connections", subtype="total", channel_id=None, count=100),
                Statistics(bucket_ts=base, type="sse_connections", subtype="unique_users", channel_id=None, count=50),
                Statistics(bucket_ts=base, type="sse_connections", subtype="heat", channel_id=None, count=3),
            ]
        )
        await db_session.commit()

        series = await service.get_chart_series(
            StatsType.SSE_CONNECTIONS, period=StatsPeriod.TEN_MIN, dt_from=base, dt_to=base + timedelta(seconds=600)
        )

        # total/unique_users не вытесняют per-channel линии
        assert [name for name, _ in series] == ["heat"]
        assert series[0][1] == [(base, 3)]

    async def test_no_data(self, db_session, fake, service):
        base = _floor_to_bucket(datetime.now(UTC) - timedelta(hours=2), 600)
        assert await service.get_chart_series("command_handled", dt_from=base, dt_to=base + timedelta(minutes=10)) == []


class TestUsersCountChart:
    @staticmethod
    async def add_user(db_session, login: str, twitch_id: str, created_at: datetime) -> None:
        db_session.add(
            User(
                twitch_id=twitch_id,
                login_name=login,
                profile_image_url="https://example.com/a.png",
                access_token="t",
                refresh_token="t",
                created_at=created_at,
            )
        )
        await db_session.commit()

    async def test_cumulative_carry_forward(self, db_session, fake, service):
        base = _floor_to_bucket(datetime.now(UTC) - timedelta(hours=1), 600)
        await self.add_user(db_session, "old", "900001", base.replace(tzinfo=None) - timedelta(hours=1))
        await self.add_user(db_session, "new", "900002", base.replace(tzinfo=None))

        points = await service.get_users_count_chart(
            period=StatsPeriod.TEN_MIN, dt_from=base, dt_to=base + timedelta(seconds=1800)
        )

        # base_count=1 (old); в первом бакете +1 (new), дальше carry-forward
        assert [v for _, v in points] == [2, 2, 2]

    async def test_min_dt_clamp(self, db_session, fake, service):
        points = await service.get_users_count_chart(
            period=StatsPeriod.TEN_MIN, dt_from=None, dt_to=datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
        )
        assert points, "диапазон должен быть не пуст"
        assert points[0][0] >= StatisticsService.USERS_COUNT_MIN_DT

    async def test_empty_range(self, db_session, fake, service):
        dt = datetime.now(UTC)
        assert await service.get_users_count_chart(dt_from=dt, dt_to=dt) == []
