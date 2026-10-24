# ruff: noqa: S101, ARG002

"""Юнит-тесты SSEManager: учёт подключений, broadcast, фильтры, grace-период, snapshot."""

import json

import pytest

from services.sse_manager import SSE_FILTER_MAX_LEN, SSE_GRACE_TTL_S, SSEManager, _grace_key
from tests.unit.fixtures.fake_redis import FakeRedis
from utils.enums import SSEChannel


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def manager(redis):
    return SSEManager(redis=redis)


def _msg_json(text: str) -> str:
    return json.dumps({"text": text})


class TestConnectDisconnect:
    async def test_connect_registers_connection(self, manager):
        conn = await manager.connect(1, SSEChannel.AI_STICKER)

        assert await manager.has_clients(1, SSEChannel.AI_STICKER)
        assert conn.filter_re is None

    async def test_connect_deletes_stale_grace_key(self, manager, redis):
        key = _grace_key(1, SSEChannel.AI_STICKER)
        await redis.setex(key, SSE_GRACE_TTL_S, "1")

        await manager.connect(1, SSEChannel.AI_STICKER)

        assert not await redis.exists(key)

    async def test_disconnect_last_client_sets_grace_key(self, manager, redis):
        conn = await manager.connect(1, SSEChannel.AI_STICKER)

        await manager.disconnect(1, SSEChannel.AI_STICKER, conn)

        key = _grace_key(1, SSEChannel.AI_STICKER)
        assert await redis.exists(key)
        assert redis.ttls[key] == SSE_GRACE_TTL_S

    async def test_disconnect_not_last_client_no_grace_key(self, manager, redis):
        first = await manager.connect(1, SSEChannel.AI_STICKER)
        await manager.connect(1, SSEChannel.AI_STICKER)  # второй живой клиент — грейс не нужен

        await manager.disconnect(1, SSEChannel.AI_STICKER, first)

        assert not await redis.exists(_grace_key(1, SSEChannel.AI_STICKER))
        assert await manager.has_clients(1, SSEChannel.AI_STICKER)

    async def test_disconnect_unknown_is_noop(self, manager):
        conn = await manager.connect(1, SSEChannel.AI_STICKER)
        await manager.disconnect(999, SSEChannel.AI_STICKER, conn)  # чужой user_id
        await manager.disconnect(1, SSEChannel.HEAT, conn)  # чужой канал


class TestHasClients:
    async def test_has_clients_false_without_grace(self, manager):
        assert not await manager.has_clients(1, SSEChannel.AI_STICKER)

    async def test_has_clients_true_during_grace_period(self, manager, redis):
        conn = await manager.connect(1, SSEChannel.AI_STICKER)
        await manager.disconnect(1, SSEChannel.AI_STICKER, conn)

        assert await manager.has_clients(1, SSEChannel.AI_STICKER)

        await redis.delete(_grace_key(1, SSEChannel.AI_STICKER))
        assert not await manager.has_clients(1, SSEChannel.AI_STICKER)

    async def test_has_clients_any_channel(self, manager):
        await manager.connect(1, SSEChannel.HEAT)

        assert await manager.has_clients(1, None)
        assert not await manager.has_clients(2, None)


class TestBroadcast:
    async def test_broadcast_delivers_to_all_connections_of_channel(self, manager):
        first = await manager.connect(1, SSEChannel.AI_STICKER)
        second = await manager.connect(1, SSEChannel.AI_STICKER)
        other_channel = await manager.connect(1, SSEChannel.HEAT)
        other_user = await manager.connect(2, SSEChannel.AI_STICKER)

        await manager.broadcast(1, SSEChannel.AI_STICKER, "event")

        assert first.queue.get_nowait() == "event"
        assert second.queue.get_nowait() == "event"
        assert other_channel.queue.empty()
        assert other_user.queue.empty()  # broadcast адресован только user_id=1

    async def test_broadcast_without_clients_is_noop(self, manager):
        await manager.broadcast(42, SSEChannel.SLOVOTRON, "event")  # не падает

    async def test_broadcast_msg_filter_passes_matching_text(self, manager):
        conn = await manager.connect(1, SSEChannel.MESSAGE, filter_pattern=r"привет|hello")

        await manager.broadcast(1, SSEChannel.MESSAGE, _msg_json("hello world"))

        assert conn.queue.get_nowait() == _msg_json("hello world")

    async def test_broadcast_msg_filter_drops_non_matching_text(self, manager):
        conn = await manager.connect(1, SSEChannel.MESSAGE, filter_pattern=r"привет")

        await manager.broadcast(1, SSEChannel.MESSAGE, _msg_json("пока"))

        assert conn.queue.empty()

    async def test_broadcast_msg_filter_falls_back_to_raw_text_for_non_json(self, manager):
        conn = await manager.connect(1, SSEChannel.MESSAGE, filter_pattern=r"plain")

        await manager.broadcast(1, SSEChannel.MESSAGE, "plain text без json")

        assert conn.queue.get_nowait() == "plain text без json"

    async def test_invalid_filter_regex_disables_filter(self, manager):
        conn = await manager.connect(1, SSEChannel.MESSAGE, filter_pattern="([)")

        assert conn.filter_re is None
        await manager.broadcast(1, SSEChannel.MESSAGE, _msg_json("что угодно"))
        assert conn.queue.qsize() == 1

    async def test_filter_pattern_is_truncated_to_max_len(self, manager):
        # 500 «a» + «(» — целиком невалидный regex; после обрезки до 500 символов валиден.
        conn = await manager.connect(1, SSEChannel.MESSAGE, filter_pattern="a" * SSE_FILTER_MAX_LEN + "(")

        assert conn.filter_re is not None

    async def test_filter_ignored_for_non_msg_channels(self, manager):
        conn = await manager.connect(1, SSEChannel.AI_STICKER, filter_pattern=r"никогда")

        assert conn.filter_re is None
        await manager.broadcast(1, SSEChannel.AI_STICKER, _msg_json("текст без совпадений"))
        assert conn.queue.qsize() == 1


class TestSnapshot:
    async def test_snapshot_counts(self, manager):
        await manager.connect(1, SSEChannel.AI_STICKER)
        await manager.connect(1, SSEChannel.AI_STICKER)  # дубль: два оверлея OBS
        await manager.connect(1, SSEChannel.HEAT)
        await manager.connect(2, SSEChannel.HEAT)

        snapshot = await manager.snapshot()

        assert snapshot["total"] == 4
        assert snapshot["unique_users"] == 2
        assert snapshot["unique_pairs"] == 3
        assert snapshot["ai-sticker"] == 2
        assert snapshot["heat"] == 2

    async def test_snapshot_empty(self, manager):
        assert await manager.snapshot() == {"total": 0, "unique_users": 0, "unique_pairs": 0}


@pytest.fixture(autouse=True)
def fake_heat(monkeypatch):
    """Autouse: подменяем HeatUpstreamConnection на весь модуль.

    Иначе любой connect(HEAT) поднимает реальный websocket-коннект с бесконечным
    `_run`-циклом и keepalive-задачей — они утекают в общий session-loop.
    """

    class FakeHeatUpstreamConnection:
        instances: list["FakeHeatUpstreamConnection"] = []

        def __init__(self, user_id, sse_manager, url, statistics=None):  # noqa: ARG002
            self.user_id = user_id
            self.url = url
            self.started = False
            self.stopped = False
            FakeHeatUpstreamConnection.instances.append(self)

        def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    FakeHeatUpstreamConnection.instances = []
    monkeypatch.setattr("services.sse_manager.HeatUpstreamConnection", FakeHeatUpstreamConnection)
    return FakeHeatUpstreamConnection


class TestHeatUpstream:
    async def test_single_heat_upstream_per_user(self, manager, fake_heat):
        await manager.connect(1, SSEChannel.HEAT)
        await manager.connect(1, SSEChannel.HEAT)

        assert len(fake_heat.instances) == 1
        assert fake_heat.instances[0].started

    async def test_heat_stopped_after_last_disconnect(self, manager, fake_heat):
        first = await manager.connect(1, SSEChannel.HEAT)
        second = await manager.connect(1, SSEChannel.HEAT)

        await manager.disconnect(1, SSEChannel.HEAT, first)
        assert not fake_heat.instances[0].stopped

        await manager.disconnect(1, SSEChannel.HEAT, second)
        assert fake_heat.instances[0].stopped

    async def test_heat_not_started_for_other_channels(self, manager, fake_heat):
        await manager.connect(1, SSEChannel.AI_STICKER)

        assert fake_heat.instances == []
