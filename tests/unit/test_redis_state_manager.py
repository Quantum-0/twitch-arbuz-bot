# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты RedisStateManager: кодирование значений, индексы, wildcard-удаления,
keyspace-listener очистки и устойчивость к RedisError."""

import asyncio
import json
from typing import Any

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from services.redis_state_manager import RedisStateManager, init_redis
from tests.unit.fixtures.fake_redis import FakePubSub, FakeRedis
from twitch.state_manager import COMMON_CHANNEL, COMMON_COMMAND, COMMON_USER, SMParam

KEY = "sm:chan:cmd:5:user"
DEFAULT_KEY = "sm:chan:cmd:5:default"
KEY2 = "sm:chan:cmd2:7:target_cooldown"
KEY3 = "sm:other:cmd:9:default"


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def sm(fake: FakeRedis) -> RedisStateManager:
    return RedisStateManager(fake)


class TestEncodeDecode:
    async def test_encode_types(self):
        assert await RedisStateManager._encode_value(1.5) == "f1.5"
        assert await RedisStateManager._encode_value(42) == "i42"
        assert await RedisStateManager._encode_value("abc") == "sabc"
        assert sorted(json.loads((await RedisStateManager._encode_value({"a", "b"}))[1:])) == ["a", "b"]
        assert await RedisStateManager._encode_value(True) == "b1"
        assert await RedisStateManager._encode_value(False) == "b0"
        assert await RedisStateManager._encode_value(None) == ""

    async def test_encode_unsupported_raises(self):
        with pytest.raises(TypeError):
            await RedisStateManager._encode_value([1, 2])  # type: ignore[arg-type]

    async def test_decode_types(self):
        assert await RedisStateManager._decode_value("sabc") == "abc"
        assert await RedisStateManager._decode_value("f1.5") == 1.5
        assert await RedisStateManager._decode_value("i42") == 42
        assert await RedisStateManager._decode_value('j["a","b"]') == ["a", "b"]
        assert await RedisStateManager._decode_value("b1") is True
        assert await RedisStateManager._decode_value("b0") is False
        assert await RedisStateManager._decode_value("") is None

    async def test_decode_unknown_prefix_raises(self):
        with pytest.raises(TypeError):
            await RedisStateManager._decode_value("x123")

    async def test_decode_bytes(self):
        assert await RedisStateManager._decode_value(b"sabc") == "abc"

    def test_ensure_str(self):
        assert RedisStateManager._ensure_str(b"abc") == "abc"
        assert RedisStateManager._ensure_str("abc") == "abc"
        with pytest.raises(TypeError):
            RedisStateManager._ensure_str(42)  # type: ignore[arg-type]


class TestSetGetState:
    async def test_str_roundtrip_and_index_sets(self, fake, sm):
        await sm.set_state("target_name", channel="chan", user=5, command="cmd", param=SMParam.USER)

        assert await sm.get_state(channel="chan", user=5, command="cmd", param=SMParam.USER) == "target_name"
        assert fake.values[KEY] == "starget_name"
        # Ключ проиндексирован во всех четырёх idx-множествах
        assert fake.smembers("idx:channel:chan") == {KEY}
        assert fake.smembers("idx:command:cmd") == {KEY}
        assert fake.smembers("idx:user:5") == {KEY}
        assert fake.smembers("idx:param:user") == {KEY}

    async def test_default_ttl_applied(self, fake, sm):
        await sm.set_state("v", channel="chan", user=5, command="cmd")
        assert fake.ttls[KEY.replace(":user", ":default")] == 4 * 60 * 60

    async def test_custom_ttl(self, fake):
        sm2 = RedisStateManager(fake, default_ttl=60)
        await sm2.set_state("v", channel="chan", user=5, command="cmd")
        assert fake.ttls["sm:chan:cmd:5:default"] == 60

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(42, 42), (1.5, 1.5), (True, True), (False, False)],
    )
    async def test_typed_roundtrip(self, sm, value, expected):
        await sm.set_state(value, channel="chan", user=5, command="cmd")
        assert await sm.get_state(channel="chan", user=5, command="cmd") == expected

    async def test_set_roundtrips_to_list(self, sm):
        # set сериализуется в JSON и возвращается списком (потребители оборачивают set(...))
        await sm.set_state({"a", "b"}, channel="chan", user=5, command="cmd")
        result = await sm.get_state(channel="chan", user=5, command="cmd")
        assert isinstance(result, list)
        assert set(result) == {"a", "b"}

    async def test_bool_is_bool_not_int(self, sm):
        # Регрессия: раньше bool кодировался как int ("i1"), ветка "b" была мёртвой
        await sm.set_state(True, channel="chan", user=5, command="cmd")
        result = await sm.get_state(channel="chan", user=5, command="cmd")
        assert result is True
        assert isinstance(result, bool)

    async def test_set_none_deletes(self, fake, sm):
        await sm.set_state("v", channel="chan", user=5, command="cmd")
        await sm.set_state(None, channel="chan", user=5, command="cmd")
        assert await sm.get_state(channel="chan", user=5, command="cmd") is None
        assert "sm:chan:cmd:5:default" not in fake.values

    async def test_get_missing_returns_none(self, sm):
        assert await sm.get_state(channel="chan", user=5, command="cmd") is None

    async def test_common_defaults_key_format(self, fake, sm):
        await sm.set_state("v")
        assert "sm:COMMON:ALL_COMMANDS:-1:default" in fake.values
        assert await sm.get_state() == "v"
        assert COMMON_CHANNEL == "COMMON"
        assert COMMON_COMMAND == "ALL_COMMANDS"
        assert COMMON_USER == -1


class TestGetFromRedis:
    async def test_exact_key_hit(self, sm):
        await sm.set_state("v1", channel="chan", user=5, command="cmd")
        data = await sm.get_from_redis(channel="chan", command="cmd", user=5, param=SMParam.DEFAULT)
        assert data == {"sm:chan:cmd:5:default": "v1"}

    async def test_exact_key_miss(self, sm):
        assert await sm.get_from_redis(channel="chan", command="cmd", user=5, param=SMParam.DEFAULT) == {}

    async def test_by_channel(self, sm):
        await sm.set_state("v1", channel="chan", user=5, command="cmd", param=SMParam.USER)
        await sm.set_state("v2", channel="chan", user=7, command="cmd2", param=SMParam.TARGET_COOLDOWN)
        data = await sm.get_from_redis(channel="chan")
        assert data == {KEY: "v1", KEY2: "v2"}

    async def test_by_channel_and_command_intersection(self, sm):
        await sm.set_state("v1", channel="chan", user=5, command="cmd", param=SMParam.USER)
        await sm.set_state("v2", channel="chan", user=7, command="cmd2", param=SMParam.TARGET_COOLDOWN)
        data = await sm.get_from_redis(channel="chan", command="cmd2")
        assert data == {KEY2: "v2"}

    async def test_by_user(self, sm):
        await sm.set_state("v1", channel="chan", user=5, command="cmd")
        await sm.set_state("v2", channel="other", user=5, command="cmd")
        data = await sm.get_from_redis(user=5)
        assert data == {"sm:chan:cmd:5:default": "v1", "sm:other:cmd:5:default": "v2"}

    async def test_no_filters_scans_all_sm_keys(self, fake, sm):
        await sm.set_state("v1", channel="chan", user=5, command="cmd")
        await sm.set_state("v2", channel="other", user=9, command="cmd")
        fake.values["statistics:bucket:x"] = "1"  # не-sm ключ не должен попасть
        data = await sm.get_from_redis()
        assert data == {"sm:chan:cmd:5:default": "v1", KEY3: "v2"}

    async def test_stale_index_entry_filtered(self, fake, sm):
        # Реальный сценарий: ключ истёк по TTL, но keyspace-событие ещё не обработано
        await sm.set_state("v1", channel="chan", user=5, command="cmd")
        await fake.delete("sm:chan:cmd:5:default")  # значение удалено, idx остался
        assert await sm.get_from_redis(channel="chan") == {}


class TestDelState:
    async def test_exact_delete(self, fake, sm):
        await sm.set_state("v", channel="chan", user=5, command="cmd")
        assert await sm.del_state(channel="chan", user=5, command="cmd") == 1
        assert "sm:chan:cmd:5:default" not in fake.values
        assert await sm.del_state(channel="chan", user=5, command="cmd") == 0

    async def test_delete_by_channel(self, fake, sm):
        await sm.set_state("v1", channel="chan", user=5, command="cmd", param=SMParam.USER)
        await sm.set_state("v2", channel="chan", user=7, command="cmd2", param=SMParam.TARGET_COOLDOWN)
        await sm.set_state("v3", channel="other", user=9, command="cmd")

        # command/user нужно явно занулить: дефолты COMMON_* ведут в exact-путь
        assert await sm.del_state(channel="chan", command=None, user=None, param=None) == 2
        assert KEY not in fake.values
        assert KEY2 not in fake.values
        assert KEY3 in fake.values

    async def test_delete_all_wildcard(self, sm):
        await sm.set_state("v1", channel="chan", user=5, command="cmd")
        await sm.set_state("v2", channel="other", user=9, command="cmd")
        assert await sm.del_state(channel=None, command=None, user=None, param=None) == 2
        assert await sm.get_from_redis() == {}

    async def test_channel_command_param_uses_common_user_exact_key(self, fake, sm):
        # Паттерн pants-розыгрыша: user опущен и в set, и в del — ключи совпадают
        await sm.set_state("User2", channel="chan", command="pants", param=SMParam.USER)
        assert "sm:chan:pants:-1:user" in fake.values
        assert await sm.del_state(channel="chan", command="pants", param=SMParam.USER) == 1
        assert "sm:chan:pants:-1:user" not in fake.values

    async def test_delete_nothing(self, sm):
        assert await sm.del_state(channel="ghost") == 0


class TestRedisOutage:
    async def test_get_returns_empty_on_redis_error(self, monkeypatch):
        broken = FakeRedis()

        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise RedisError("connection lost")

        monkeypatch.setattr(broken, "get", boom)
        monkeypatch.setattr(broken, "sinter", boom)
        monkeypatch.setattr(broken, "scan_iter", boom)
        sm2 = RedisStateManager(broken)
        assert await sm2.get_state(channel="c", user=1, command="x") is None
        assert await sm2.get_from_redis(channel="c") == {}

    async def test_set_swallows_redis_error(self, monkeypatch):
        broken = FakeRedis()

        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise RedisError("connection lost")

        monkeypatch.setattr(broken, "set", boom)
        sm2 = RedisStateManager(broken)
        await sm2.set_state("v", channel="c", user=1, command="x")  # не падает

    async def test_del_returns_zero_on_redis_error(self, monkeypatch):
        broken = FakeRedis()

        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise RedisError("connection lost")

        monkeypatch.setattr(broken, "delete", boom)
        monkeypatch.setattr(broken, "sinter", boom)
        sm2 = RedisStateManager(broken)
        assert await sm2.del_state(channel="c") == 0


class TestGetAllFromChannel:
    async def test_yields_parsed_entries(self, sm):
        await sm.set_state("winner", channel="chan", user=5, command="pants", param=SMParam.USER)
        await sm.set_state(123.5, channel="chan", user=7, command="pants", param=SMParam.TARGET_COOLDOWN)

        entries = [entry async for entry in sm.get_all_from_channel(channel="chan")]
        assert (5, "pants", SMParam.USER, "winner") in entries
        assert (7, "pants", SMParam.TARGET_COOLDOWN, 123.5) in entries
        assert len(entries) == 2

    async def test_non_numeric_user_stays_str(self, sm):
        await sm.set_state("v", channel="chan", user="somename", command="cmd")
        entries = [entry async for entry in sm.get_all_from_channel(channel="chan")]
        assert entries == [("somename", "cmd", SMParam.DEFAULT, "v")]

    async def test_malformed_keys_skipped(self, fake, sm):
        await sm.set_state("v", channel="chan", user=5, command="cmd")
        # Битый ключ: 4 части вместо 5
        fake.values["sm:a:b:c"] = "sv"
        fake.sets.setdefault("idx:channel:chan", set()).add("sm:a:b:c")
        entries = [entry async for entry in sm.get_all_from_channel(channel="chan")]
        assert entries == [(5, "cmd", SMParam.DEFAULT, "v")]


class TestKeyListener:
    @staticmethod
    def _expired_event(key: str) -> dict[str, str]:
        return {"type": "message", "data": key, "channel": "__keyevent@0__:expired"}

    async def test_expired_key_cleans_index_sets(self, fake, sm, monkeypatch):
        await sm.set_state("v", channel="chan", user=5, command="cmd")
        assert DEFAULT_KEY in fake.smembers("idx:channel:chan")
        ps = FakePubSub()
        ps.queue = [self._expired_event(DEFAULT_KEY), asyncio.CancelledError()]
        monkeypatch.setattr(fake, "pubsub", lambda: ps)

        await RedisStateManager.redis_event_listener(fake)

        assert DEFAULT_KEY not in fake.smembers("idx:channel:chan")
        assert DEFAULT_KEY not in fake.smembers("idx:command:cmd")
        assert DEFAULT_KEY not in fake.smembers("idx:user:5")
        assert DEFAULT_KEY not in fake.smembers("idx:param:default")
        assert ps.closed

    async def test_non_sm_key_ignored(self, fake, sm, monkeypatch):
        await sm.set_state("v", channel="chan", user=5, command="cmd")
        ps = FakePubSub()
        ps.queue = [self._expired_event("statistics:bucket:x"), asyncio.CancelledError()]
        monkeypatch.setattr(fake, "pubsub", lambda: ps)

        await RedisStateManager.redis_event_listener(fake)

        assert DEFAULT_KEY in fake.smembers("idx:channel:chan")  # не тронут
        assert ps.closed

    async def test_malformed_sm_key_ignored(self, fake, sm, monkeypatch):
        await sm.set_state("v", channel="chan", user=5, command="cmd")
        ps = FakePubSub()
        ps.queue = [self._expired_event("sm:a:b:c"), asyncio.CancelledError()]
        monkeypatch.setattr(fake, "pubsub", lambda: ps)

        await RedisStateManager.redis_event_listener(fake)

        assert fake.smembers("idx:channel:chan") == {DEFAULT_KEY}

    async def test_redis_error_reconnects(self, fake, monkeypatch):
        async def fast_sleep(delay: float) -> None:  # noqa: ARG001
            return None

        monkeypatch.setattr(asyncio, "sleep", fast_sleep)
        ps1 = FakePubSub()
        ps1.queue = [RedisError("connection lost"), asyncio.CancelledError()]
        ps2 = FakePubSub()
        ps2.queue = [asyncio.CancelledError()]
        made = iter([ps1, ps2])
        monkeypatch.setattr(fake, "pubsub", lambda: next(made))

        await RedisStateManager.redis_event_listener(fake)

        # После ошибки создана новая подписка, обе закрыты
        assert ps1.closed
        assert ps2.closed
        assert set(ps2.subscribed) == {"__keyevent@0__:expired", "__keyevent@0__:del"}

    async def test_lifespan_cancels_listener(self, fake, sm, monkeypatch):
        ps = FakePubSub()
        ps.hang_when_empty = True
        monkeypatch.setattr(fake, "pubsub", lambda: ps)

        async with sm.lifespan():
            await asyncio.sleep(0.05)
            assert set(ps.subscribed) == {"__keyevent@0__:expired", "__keyevent@0__:del"}

        # После выхода из lifespan слушатель остановлен и отписан
        assert ps.closed
        assert ps.unsubscribe_calls


class TestInitRedis:
    async def test_decode_responses_default(self):
        client = init_redis("redis://localhost:6379/0")
        try:
            assert isinstance(client, Redis)
            assert client.connection_pool.connection_kwargs["decode_responses"] is True
        finally:
            await client.aclose()

    async def test_binary_mode(self):
        client = init_redis("redis://localhost:6379/0", binary=True)
        try:
            assert client.connection_pool.connection_kwargs["decode_responses"] is False
        finally:
            await client.aclose()
