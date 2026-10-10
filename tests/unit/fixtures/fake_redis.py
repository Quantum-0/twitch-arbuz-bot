"""Минимальный асинхронный фейк Redis для сервисов, использующих aioredis.

Покрывает только подмножество API, нужное ``ServerMetricsService``
(hgetall/hset/rpush/lrange/ltrim/delete/get/set/pipeline).
"""

from typing import Any


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self._redis = redis

    def rpush(self, name: str, *values: str) -> "FakePipeline":
        self._redis._rpush(name, *values)
        return self

    def expire(self, name: str, seconds: int) -> "FakePipeline":
        self._redis.ttls[name] = seconds
        return self

    def set(self, name: str, value: str, ex: int | None = None) -> "FakePipeline":  # noqa: ARG002
        self._redis.values[name] = value
        return self

    def hset(self, name: str, mapping: dict[str, Any] | None = None) -> "FakePipeline":
        self._redis._hset(name, mapping or {})
        return self

    async def execute(self) -> list[Any]:
        return []


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}

    def _rpush(self, name: str, *values: str) -> int:
        self.lists.setdefault(name, []).extend(values)
        return len(self.lists[name])

    def _hset(self, name: str, mapping: dict[str, Any]) -> int:
        target = self.hashes.setdefault(name, {})
        target.update({k: str(v) for k, v in mapping.items()})
        return len(target)

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
            if name in self.values or name in self.lists or name in self.hashes:
                removed += 1
            self.values.pop(name, None)
            self.lists.pop(name, None)
            self.hashes.pop(name, None)
        return removed

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.values[name] = value

    def pipeline(self, transaction: bool = False) -> FakePipeline:  # noqa: ARG002
        return FakePipeline(self)


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
