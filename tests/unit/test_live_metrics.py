# ruff: noqa: S101, SLF001, ARG001, ARG002

"""Юнит-тесты live-метрик карты потоков /flowviz.

LiveRegistry — кумулятивные счётчики и скорости; CountingRedis/Pipeline —
подсчёт команд Redis; интеграция с StatisticsService (bump_persisted,
flush_live_delta, count_active_channels); перенос дельт flush_live_metrics.
"""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from schemas.api import StatsType
from services import live_metrics as lm
from services.live_metrics import LiveRegistry, live_metrics
from services.redis_state_manager import CountingPipeline, CountingRedis, init_redis
from services.statistics import StatisticsService
from tests.unit.fixtures.fake_redis import FakeRedis, SessionCtx


@pytest.fixture(autouse=True)
def clean_registry():
    live_metrics().reset()
    yield
    live_metrics().reset()


class TestLiveRegistry:
    def test_bump_accumulates_totals_and_pending(self):
        reg = LiveRegistry()
        reg.bump("db_queries", "SELECT")
        reg.bump("db_queries", "SELECT")
        reg.bump("db_queries", "SELECT", n=3)
        pending = reg.take_pending()
        assert pending == {("db_queries", "SELECT"): 5}
        # второй take — пусто (дельты забраны)
        assert reg.take_pending() == {}

    def test_bump_persisted_skips_pending(self):
        reg = LiveRegistry()
        reg.bump_persisted("message_incoming", "", 2)
        assert reg.take_pending() == {}
        # но в totals попало — rates это видят: (2+4-2)/2с = 2/с
        reg.sample(now=0.0)
        reg.bump_persisted("message_incoming", "", 4)
        reg.sample(now=2.0)
        assert reg.rates(window=2.0, now=2.0) == {("message_incoming", ""): 2.0}

    def test_rates_diff_over_window(self):
        reg = LiveRegistry()
        reg.bump("redis_ops", "get", 10)
        reg.sample(now=0.0)
        reg.bump("redis_ops", "get", 30)
        reg.bump("redis_ops", "set", 4)
        reg.sample(now=2.0)
        rates = reg.rates(window=2.0, now=2.0)
        assert rates == {("redis_ops", "get"): 15.0, ("redis_ops", "set"): 2.0}

    def test_rates_without_samples_empty(self):
        reg = LiveRegistry()
        assert reg.rates() == {}

    def test_sample_respects_min_interval(self):
        reg = LiveRegistry()
        reg.sample(now=0.0)
        reg.sample(now=0.5)  # слишком рано — игнор
        reg.sample(now=2.0)
        assert len(reg._samples) == 2

    def test_stats_type_values_are_valid_subtypes(self):
        # новые метрики — обычные counter: не должны попадать в спец-множества
        from services.statistics import GAUGE_TYPES, SUM_TYPES, TIMING_TYPES

        new_types = {
            StatsType.HTTP_REQUESTS,
            StatsType.DB_QUERIES,
            StatsType.REDIS_OPS,
            StatsType.MQTT_PUBLISH,
            StatsType.MQTT_MESSAGES,
            StatsType.EVENTSUB_EVENTS,
            StatsType.SSE_EVENTS,
            StatsType.TWITCH_API_CALLS,
        }
        assert not new_types & GAUGE_TYPES
        assert not new_types & TIMING_TYPES
        assert not new_types & SUM_TYPES


class TestCountingRedis:
    async def test_execute_command_bumps(self, monkeypatch):
        async def fake_send(self, *args: Any, **kwargs: Any) -> str:
            return "OK"

        monkeypatch.setattr("redis.asyncio.Redis.execute_command", fake_send)
        client = init_redis("redis://localhost:6399/0")
        assert isinstance(client, CountingRedis)
        await client.execute_command("PING")
        await client.execute_command("GET", "x")
        assert live_metrics().take_pending() == {
            ("redis_ops", "ping"): 1,
            ("redis_ops", "get"): 1,
        }

    def test_pipeline_is_counting_and_buffers(self):
        client = init_redis("redis://localhost:6399/0")
        pipe = client.pipeline(transaction=False)
        assert isinstance(pipe, CountingPipeline)
        # буферизация команды не ходит в Redis, но уже посчитана
        pipe.hincrby("k", "f", 1)
        pipe.expire("k", 60)
        assert live_metrics().take_pending() == {
            ("redis_ops", "hincrby"): 1,
            ("redis_ops", "expire"): 1,
        }


class TestStatisticsIntegration:
    async def test_inc_bumps_persisted_not_pending(self, db_session):
        service = StatisticsService(lambda: SessionCtx(db_session), FakeRedis())
        service.inc(StatsType.MESSAGE_INCOMING, amount=2)
        assert live_metrics().take_pending() == {}
        reg = live_metrics()
        assert reg._totals == {("message_incoming", ""): 2}

    async def test_flush_live_delta_writes_redis(self, db_session):
        fake = FakeRedis()
        service = StatisticsService(lambda: SessionCtx(db_session), fake)
        await service.flush_live_delta(str(StatsType.DB_QUERIES), "SELECT", 3)
        await service.flush_live_delta(str(StatsType.DB_QUERIES), "SELECT", 4)
        assert len(fake.hashes) == 1  # один бакет текущих 10 минут
        hash_data = next(iter(fake.hashes.values()))
        field = f"{StatsType.DB_QUERIES}:SELECT:"
        assert hash_data == {field: "7"}

    async def test_count_active_channels(self, db_session):
        from datetime import UTC, datetime

        from services.statistics import BUCKET_SECONDS, _floor_to_bucket

        fake = FakeRedis()
        service = StatisticsService(lambda: SessionCtx(db_session), fake)
        bucket = _floor_to_bucket(datetime.now(UTC), BUCKET_SECONDS)
        key = service._channels_key("incoming", bucket)
        await fake.sadd(key, "1", "2", "3")
        assert await service.count_active_channels() == 3
        # несуществующий subtype → 0 (не исключение)
        assert await service.count_active_channels("outgoing") == 0


class TestFlushLiveMetrics:
    async def test_deltas_transferred_and_cleared(self, monkeypatch):
        live_metrics().bump(StatsType.REDIS_OPS, "get", 5)
        live_metrics().bump(StatsType.DB_QUERIES, "SELECT", 2)
        calls: list[tuple[str, str, int]] = []

        async def fake_flush(type_: str, subtype: str, amount: int) -> None:
            calls.append((type_, subtype, amount))

        fake_stats = SimpleNamespace(flush_live_delta=fake_flush)
        monkeypatch.setattr("container_runtime.get_container", lambda: SimpleNamespace(statistics=lambda: fake_stats))
        await lm.flush_live_metrics()
        assert sorted(calls) == [("db_queries", "SELECT", 2), ("redis_ops", "get", 5)]
        assert live_metrics().take_pending() == {}

    async def test_single_failure_does_not_break_loop(self, monkeypatch):
        live_metrics().bump(StatsType.REDIS_OPS, "get", 5)

        async def failing(type_: str, subtype: str, amount: int) -> None:
            raise RuntimeError("redis down")

        fake_stats = SimpleNamespace(flush_live_delta=failing)
        monkeypatch.setattr("container_runtime.get_container", lambda: SimpleNamespace(statistics=lambda: fake_stats))
        # дельта не потеряна из live-итогов, исключение поймано
        await lm.flush_live_metrics()
        assert live_metrics().take_pending() == {}


class TestHttpxTwitchCounter:
    def test_bump_by_host(self):
        lm._bump_twitch_httpx("api.twitch.tv")
        lm._bump_twitch_httpx("id.twitch.tv")
        lm._bump_twitch_httpx("example.com")  # не Twitch — не считается
        lm._bump_twitch_httpx(None)
        assert live_metrics().take_pending() == {
            ("twitch_api_calls", "helix"): 1,
            ("twitch_api_calls", "oauth"): 1,
        }

    def test_install_is_idempotent(self, monkeypatch):
        # monkeypatch зафиксирует оригинал и восстановит его после теста
        monkeypatch.setattr(httpx.AsyncClient, "send", httpx.AsyncClient.send)
        lm.install_httpx_twitch_counter()
        first = httpx.AsyncClient.send
        lm.install_httpx_twitch_counter()
        assert httpx.AsyncClient.send is first
