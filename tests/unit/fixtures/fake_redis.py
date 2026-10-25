"""Минимальный асинхронный фейк Redis для сервисов, использующих aioredis.

Покрывает API, нужный тестам (строки/хэши/списки + множества sadd/sinter/srem
для RedisStateManager, scan_iter, mget, pubsub для keyspace-listener).
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import AsyncIterator
from typing import Any


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    def rpush(self, name: str, *values: str) -> FakePipeline:
        self._redis._rpush(name, *values)
        return self

    def expire(self, name: str, seconds: int) -> FakePipeline:
        self._redis.ttls[name] = seconds
        return self

    def set(self, name: str, value: str, ex: int | None = None) -> FakePipeline:  # noqa: ARG002
        self._redis.values[name] = value
        return self

    def hset(self, name: str, mapping: dict[str, Any] | None = None) -> FakePipeline:
        self._redis._hset(name, mapping or {})
        return self

    def srem(self, name: str, *values: str) -> FakePipeline:
        self._redis._srem(name, *values)
        return self

    async def execute(self) -> list[Any]:
        return []


class FakePubSub:
    """Фейк pubsub-подписки для RedisStateManager.redis_event_listener.

    ``queue`` — очередь ответов get_message: dict-сообщение или исключение
    (RedisError / CancelledError). Пустая очередь: hang_when_empty=True —
    блокируется навсегда (для теста lifespan), иначе возвращает None.
    """

    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.hang_when_empty = False
        self.subscribed: list[str] = []
        self.unsubscribe_calls: list[tuple[str, ...]] = []
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.subscribed.extend(channels)

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float | None = None) -> Any:  # noqa: ARG002
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        if self.hang_when_empty:
            await asyncio.Event().wait()
        # Имитация ожидания pubsub-сообщения: без паузы тесты уйдут в busy-loop
        await asyncio.sleep(0.01)
        return None

    async def unsubscribe(self, *channels: str) -> None:
        self.unsubscribe_calls.append(channels)

    async def aclose(self) -> None:
        self.closed = True


class _SelfCtx:
    """async with client.client() as c — возвращает сам клиент."""

    def __init__(self, obj: Any) -> None:
        self._obj = obj

    async def __aenter__(self) -> Any:
        return self._obj

    async def __aexit__(self, *args: Any) -> bool:
        return False


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttls: dict[str, int] = {}
        self.pubsubs: list[FakePubSub] = []

    def _rpush(self, name: str, *values: str) -> int:
        self.lists.setdefault(name, []).extend(values)
        return len(self.lists[name])

    def _hset(self, name: str, mapping: dict[str, Any]) -> int:
        target = self.hashes.setdefault(name, {})
        target.update({k: str(v) for k, v in mapping.items()})
        return len(target)

    def _srem(self, name: str, *values: str) -> int:
        members = self.sets.get(name)
        if not members:
            return 0
        removed = sum(1 for v in values if v in members)
        members.difference_update(values)
        return removed

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self.hashes.get(name, {}))

    async def hset(self, name: str, mapping: dict[str, Any] | None = None) -> int:
        return self._hset(name, mapping or {})

    async def rpush(self, name: str, *values: str) -> int:
        return self._rpush(name, *values)

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        items = self.lists.get(name, [])
        return items[start:] if end == -1 else items[start : end + 1]

    async def ltrim(self, name: str, start: int, end: int) -> None:
        items = self.lists.get(name, [])
        n = len(items)

        def idx(i: int) -> int:
            return i if i >= 0 else n + i

        s, e = idx(start), idx(end)
        self.lists[name] = items[s : e + 1] if s <= e else []

    async def delete(self, *names: str) -> int:
        removed = 0
        for name in names:
            if name in self.values or name in self.lists or name in self.hashes or name in self.sets:
                removed += 1
            self.values.pop(name, None)
            self.lists.pop(name, None)
            self.hashes.pop(name, None)
            self.sets.pop(name, None)
        return removed

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> None:
        self.values[name] = value
        if ex is not None:
            self.ttls[name] = ex

    async def setex(self, name: str, seconds: int, value: str) -> None:
        self.values[name] = value
        self.ttls[name] = seconds

    async def exists(self, *names: str) -> int:
        return sum(1 for n in names if n in self.values or n in self.lists or n in self.hashes or n in self.sets)

    async def incr(self, name: str) -> int:
        value = int(self.values.get(name, "0")) + 1
        self.values[name] = str(value)
        return value

    async def expire(self, name: str, seconds: int) -> bool:  # noqa: ARG002
        self.ttls[name] = seconds
        return True

    async def ttl(self, name: str) -> int:
        if name not in self.values:
            return -2
        return self.ttls.get(name, -1)

    def pipeline(self, transaction: bool = False) -> FakePipeline:  # noqa: ARG002
        return FakePipeline(self)

    # --- Множества (индексы RedisStateManager) ---

    async def sadd(self, name: str, *values: str) -> int:
        members = self.sets.setdefault(name, set())
        new = [v for v in values if v not in members]
        members.update(new)
        return len(new)

    async def sinter(self, keys: Any, *args: str) -> set[str]:
        # redis-py позволяет первым аргументом список ключей
        names = [*keys, *args] if isinstance(keys, (list, tuple, set)) else [keys, *args]
        result: set[str] | None = None
        for n in names:
            members = self.sets.get(n, set())
            result = set(members) if result is None else result & members
            if not result:
                return set()
        return result or set()

    def smembers(self, name: str) -> set[str]:
        return set(self.sets.get(name, set()))

    async def srem(self, name: str, *values: str) -> int:
        return self._srem(name, *values)

    async def mget(self, keys: Any, *args: str) -> list[str | None]:
        # redis-py позволяет первым аргументом список ключей
        all_keys = [*keys, *args] if isinstance(keys, (list, tuple, set)) else [keys, *args]
        return [self.values.get(k) for k in all_keys]

    async def scan_iter(self, match: str | None = None) -> AsyncIterator[str]:
        keys = [*self.values, *self.lists, *self.hashes, *self.sets]
        for key in keys:
            if match is None or fnmatch.fnmatchcase(key, match):
                yield key

    # --- Pub/Sub (keyspace-listener) ---

    def pubsub(self) -> FakePubSub:
        ps = FakePubSub()
        self.pubsubs.append(ps)
        return ps

    def client(self) -> _SelfCtx:
        return _SelfCtx(self)


class SessionCtx:
    """Обёртка, чтобы ``async with factory() as session:`` работал на фикстуре db_session."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *args: Any) -> bool:
        return False


def session_factory(session: Any):
    """«Фабрика сессий» из фикстуры db_session для сервисов с db_session_factory."""
    return lambda: SessionCtx(session)
