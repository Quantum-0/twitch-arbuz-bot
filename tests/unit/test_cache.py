# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты Redis-кеша (services/cache.py): as_cached с pickle-сериализацией,
set/get-примитивы, NX-дедуп и rate-limit с самолечением TTL."""

import pickle  # noqa: S301
from typing import Any

import pytest
from redis.exceptions import RedisError

from services.cache import Cache
from tests.unit.fixtures.fake_redis import FakeRedis


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def binary_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def cache(redis, binary_redis) -> Cache:
    return Cache(redis, binary_redis)


class BrokenRedis(FakeRedis):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def _boom(self, *args: Any, **kwargs: Any) -> Any:
        raise self._exc

    async def get(self, name):
        raise self._exc  # type: ignore[misc]

    async def set(self, name, value, ex=None, nx=None):  # type: ignore[assignment]
        raise self._exc  # type: ignore[misc]

    async def setex(self, name, seconds, value):
        raise self._exc  # type: ignore[misc]

    async def smembers(self, name):
        raise self._exc  # type: ignore[misc]

    async def delete(self, *names):
        raise self._exc  # type: ignore[misc]

    async def incr(self, name):
        raise self._exc

    def pipeline(self, transaction: bool = False):  # noqa: ARG002
        raise self._exc  # type: ignore[misc]


class TestAsCached:
    async def test_cache_miss_then_hit(self, cache, binary_redis):
        calls = []

        async def fetch_data(user_id: int) -> dict:
            calls.append(user_id)
            return {"user": user_id}

        first = await cache.as_cached(fetch_data, 7)
        assert first == {"user": 7}
        assert calls == [7]

        # Значение закешировано (pickle) — функция не вызывается
        second = await cache.as_cached(fetch_data, 7)
        assert second == {"user": 7}
        assert calls == [7]
        assert len(binary_redis.values) == 1

    async def test_cache_key_depends_on_args(self, cache):
        async def fetch(user_id: int) -> int:
            return user_id

        await cache.as_cached(fetch, 1)
        await cache.as_cached(fetch, 2)
        assert len(cache._rb.values) == 2

    async def test_cache_ttl_applied(self, cache, binary_redis):
        # Регрессия: cache_ttl раньше игнорировался, всегда писался 3600
        async def fetch() -> int:
            return 1

        await cache.as_cached(fetch, cache_ttl=42)
        key = next(iter(binary_redis.values))
        assert binary_redis.ttls[key] == 42

    async def test_default_ttl(self, cache, binary_redis):
        async def fetch() -> int:
            return 1

        await cache.as_cached(fetch)
        key = next(iter(binary_redis.values))
        assert binary_redis.ttls[key] == 120

    async def test_generate_key_format(self, cache):
        async def my_func(a: int, b: str = "x") -> None:
            return None

        key1 = cache._generate_key(my_func, (1,), {"b": "y"})
        key2 = cache._generate_key(my_func, (1,), {"b": "z"})
        assert key1.startswith("cache:as_cached:my_func:")
        assert key1 != key2


class TestSets:
    async def test_set_and_get_set(self, cache, redis):
        await cache.set_set("followers", {"a", "b"})
        assert redis.sets["cache:followers"] == {"a", "b"}
        assert redis.ttls["cache:followers"] == 3600
        assert await cache.get_set("followers") == {"a", "b"}

    async def test_set_set_overwrites(self, cache, redis):
        await cache.set_set("x", {"a"})
        await cache.set_set("x", {"b"})
        assert redis.sets["cache:x"] == {"b"}

    async def test_set_empty_clears(self, cache, redis):
        await cache.set_set("x", {"a"})
        await cache.set_set("x", set())
        assert "cache:x" not in redis.sets
        assert await cache.get_set("x") == set()

    async def test_set_set_error_swallowed(self):
        broken = BrokenRedis(RedisError("down"))
        cache = Cache(FakeRedis(), FakeRedis())
        cache._r = broken
        await cache.set_set("x", {"a"})  # no_error=True

    async def test_set_set_error_raises(self):
        cache = Cache(FakeRedis(), FakeRedis())
        cache._r = BrokenRedis(RedisError("down"))
        with pytest.raises(RedisError):
            await cache.set_set("x", {"a"}, no_error=False)

    async def test_get_set_error_returns_empty(self):
        cache = Cache(FakeRedis(), FakeRedis())
        cache._r = BrokenRedis(RedisError("down"))
        assert await cache.get_set("x") == set()
        with pytest.raises(RedisError):
            await cache.get_set("x", no_error=False)


class TestStrings:
    async def test_set_get_str(self, cache, redis):
        await cache.set_str("k", "v")
        assert redis.values["cache:k"] == "v"
        assert redis.ttls["cache:k"] == 3600
        assert await cache.get_str("k") == "v"

    async def test_get_str_missing(self, cache):
        assert await cache.get_str("nope") is None

    async def test_str_errors(self):
        cache = Cache(FakeRedis(), FakeRedis())
        cache._r = BrokenRedis(RedisError("down"))
        assert await cache.get_str("k") is None
        await cache.set_str("k", "v")
        await cache.delete("k")
        with pytest.raises(RedisError):
            await cache.get_str("k", no_error=False)

    async def test_delete(self, cache, redis):
        await cache.set_str("k", "v")
        await cache.delete("k")
        assert await cache.get_str("k") is None


class TestSetNx:
    async def test_first_write_wins(self, cache, redis):
        assert await cache.set_str_nx("dedup", "a") is True
        assert await cache.set_str_nx("dedup", "b") is False  # ключ уже занят
        assert redis.values["cache:dedup"] == "a"

    async def test_redis_error_returns_true(self):
        """Дедуп пропускается при недоступном Redis (лучше дубль, чем потеря)."""
        cache = Cache(FakeRedis(), FakeRedis())
        cache._r = BrokenRedis(RedisError("down"))
        assert await cache.set_str_nx("dedup", "a") is True

    async def test_redis_error_can_raise(self):
        cache = Cache(FakeRedis(), FakeRedis())
        cache._r = BrokenRedis(RedisError("down"))
        with pytest.raises(RedisError):
            await cache.set_str_nx("dedup", "a", no_error=False)


class TestRateLimit:
    async def test_under_limit_allowed(self, cache, redis):
        for _ in range(3):
            assert await cache.check_rate_limit("rl", limit=3, window_s=60) is True
        assert redis.values["rate_limit:rl"] == "3"
        assert redis.ttls["rate_limit:rl"] == 60

    async def test_over_limit_denied(self, cache):
        for _ in range(3):
            assert await cache.check_rate_limit("rl", limit=3, window_s=60) is True
        assert await cache.check_rate_limit("rl", limit=3, window_s=60) is False

    async def test_ttl_self_healing(self, cache, redis):
        # Ключ остался без TTL (крэш между INCR и EXPIRE) — TTL восстанавливается
        redis.values["rate_limit:rl"] = "5"
        redis.ttls.pop("rate_limit:rl", None)  # имитируем отсутствие TTL → -1
        await cache.check_rate_limit("rl", limit=10, window_s=30)
        assert redis.ttls["rate_limit:rl"] == 30

    async def test_redis_error_allows(self):
        """При недоступном Redis — пропускаем (fail-open)."""
        cache = Cache(FakeRedis(), FakeRedis())
        cache._r = BrokenRedis(RedisError("down"))
        assert await cache.check_rate_limit("rl", limit=1, window_s=60) is True


class TestPickleRoundtrip:
    async def test_complex_object_cached(self, cache, binary_redis):
        async def fetch() -> dict[str, list[int]]:
            return {"data": [1, 2, 3], "nested": {"a": True}}

        await cache.as_cached(fetch)
        raw = next(iter(binary_redis.values.values()))
        assert pickle.loads(raw) == {"data": [1, 2, 3], "nested": {"a": True}}  # noqa: S301
