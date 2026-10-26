# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты Heat-upstream: проксирование WS-сообщений в SSE, метрики объёма,
реконнект с backoff и корректная остановка задачи."""

import asyncio
from typing import Any

import pytest

import services.heat_upstream as hu
from schemas.api import StatsType
from utils.enums import SSEChannel


class FakeWS:
    def __init__(self, messages: list[Any]) -> None:
        self._messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        raise StopAsyncIteration


class FakeWSConnection:
    """async with websockets.connect(...) — CM вокруг фейкового WS."""

    def __init__(self, ws: FakeWS | None, error: Exception | None = None) -> None:
        self._ws = ws
        self._error = error

    async def __aenter__(self):
        if self._error:
            raise self._error
        return self._ws

    async def __aexit__(self, *args: Any) -> bool:
        return False


class FakeSSEManager:
    def __init__(self) -> None:
        self.broadcasts: list[tuple[int, str, Any]] = []

    async def broadcast(self, user_id: int, channel: str, message: Any) -> None:
        self.broadcasts.append((user_id, channel, message))


class FakeStatistics:
    def __init__(self) -> None:
        self.counters: list[str] = []
        self.timings: list[tuple[str, int]] = []

    def inc(self, type_, subtype: str = "", channel_id: int | None = None, amount: int = 1):  # noqa: ARG002
        self.counters.append(str(type_))

    def inc_timing(self, type_, subtype: str = "", value_ms: int = 0, **kwargs):  # noqa: ARG002
        self.timings.append((str(type_), value_ms))


class ConnectScript:
    """Скрипт подключений: каждое обращение выдаёт следующий FakeWSConnection."""

    def __init__(self, steps: list[FakeWSConnection | None]) -> None:
        self._steps = list(steps)
        self.calls = 0

    def __call__(self, url: str, **kwargs: Any) -> FakeWSConnection:
        assert url == "wss://heat.example/heat"
        self.calls += 1
        step = self._steps.pop(0) if self._steps else FakeWSConnection(None, asyncio.CancelledError())
        if step is None:
            return FakeWSConnection(None, asyncio.CancelledError())
        return step


@pytest.fixture
def sse() -> FakeSSEManager:
    return FakeSSEManager()


def make_upstream(sse, statistics=None) -> hu.HeatUpstreamConnection:
    return hu.HeatUpstreamConnection(42, sse, "wss://heat.example/heat", statistics=statistics)


async def run_upstream(upstream, script: ConnectScript, monkeypatch) -> None:
    monkeypatch.setattr(hu.websockets, "connect", script)
    task = asyncio.create_task(upstream._run())
    for _ in range(100):
        if task.done():
            break
        await asyncio.sleep(0.01)
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class TestRun:
    async def test_messages_broadcasted_with_stats(self, sse, monkeypatch):
        statistics = FakeStatistics()
        upstream = make_upstream(sse, statistics=statistics)

        script = ConnectScript(
            [
                FakeWSConnection(FakeWS(["hello", b"\xff\xfe-bytes"])),
                None,  # следующий коннект — CancelledError → выход из цикла
            ]
        )
        await run_upstream(upstream, script, monkeypatch)

        assert sse.broadcasts == [
            (42, SSEChannel.HEAT, "hello"),
            (42, SSEChannel.HEAT, "-bytes"),  # errors="ignore": невалидные байты выброшены
        ]
        assert statistics.counters == [str(StatsType.HEAT_PROXY_MESSAGES)] * 2
        assert statistics.timings[0] == (str(StatsType.HEAT_PROXY_BYTES), len("hello"))
        assert statistics.timings[1][1] == len("-bytes")
        assert script.calls == 2  # реконнект после закрытия WS

    async def test_no_statistics_no_crash(self, sse, monkeypatch):
        upstream = make_upstream(sse)
        script = ConnectScript([FakeWSConnection(FakeWS(["x"]))])
        await run_upstream(upstream, script, monkeypatch)
        assert sse.broadcasts == [(42, SSEChannel.HEAT, "x")]

    async def test_reconnect_backoff_on_errors(self, sse, monkeypatch):
        sleeps: list[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(delay: float) -> None:
            if delay >= 0.9:  # backoff-паузы — мгновенно, с записью
                sleeps.append(round(delay, 2))
                await real_sleep(0)
            else:  # служебные паузы теста — реальные
                await real_sleep(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        upstream = make_upstream(sse)

        script = ConnectScript(
            [
                FakeWSConnection(None, OSError("refused")),
                FakeWSConnection(None, OSError("refused")),
                None,  # CancelledError → выход
            ]
        )
        await run_upstream(upstream, script, monkeypatch)

        assert script.calls == 3
        assert len(sleeps) == 2
        # backoff 1..1.5, затем 2..2.5
        assert 1 <= sleeps[0] <= 1.5
        assert 2 <= sleeps[1] <= 2.5
        assert sse.broadcasts == []


class TestStartStop:
    async def test_start_creates_single_task(self, sse):
        upstream = make_upstream(sse)
        upstream.start()
        task = upstream._task
        upstream.start()  # повторный start — no-op
        assert upstream._task is task
        await upstream.stop()
        await asyncio.sleep(0)  # даём отмене отработать
        assert task.cancelled() or task.done()

    async def test_stop_without_start(self, sse):
        upstream = make_upstream(sse)
        await upstream.stop()  # не падает

    async def test_start_stop_roundtrip(self, sse, monkeypatch):
        monkeypatch.setattr(hu.websockets, "connect", ConnectScript([]))
        upstream = make_upstream(sse)

        async def run_and_stop():
            upstream.start()
            await asyncio.sleep(0.02)
            await upstream.stop()

        await run_and_stop()
        assert upstream._task is None

    async def test_run_exits_on_stop_event(self, sse, monkeypatch):
        """stop() во время активного соединения останавливает цикл проксирования."""
        upstream = make_upstream(sse)
        connected = asyncio.Event()
        released = asyncio.Event()

        class HangingWS:
            def __aiter__(self):
                return self

            async def __anext__(self):
                connected.set()
                await released.wait()
                raise StopAsyncIteration

        class OneConnection:
            def __call__(self, url: str, **kwargs: Any) -> FakeWSConnection:
                return FakeWSConnection(HangingWS())  # type: ignore[arg-type]

        monkeypatch.setattr(hu.websockets, "connect", OneConnection())
        task = asyncio.create_task(upstream._run())
        await asyncio.wait_for(connected.wait(), timeout=1)
        await upstream.stop()
        released.set()
        await asyncio.gather(task, return_exceptions=True)
        assert task.done()
