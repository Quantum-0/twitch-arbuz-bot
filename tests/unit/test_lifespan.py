# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты dependencies.lifespan: старт/стоп ресурсов, MQTT-подписки,
регистрация планировщика и корректный shutdown-порядок."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import dependencies
from config import settings
from tests.unit.fixtures.fake_redis import FakeRedis
from utils.enums import MQTTTopic

EXPECTED_JOBS = {
    "update_memealerts_tokens",
    "update_twitch_tokens",
    "poll_clips",
    "reconcile_stream_subs",
    "flush_statistics",
    "cleanup_old_statistics",
    "server_metrics_tick",
    "server_metrics_flush",
    "cleanup_server_metrics",
    "snapshot_sse",
    "cleanup_overlay_eventsub",
}


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.started = False
        self.shutdown_called = False

    def add_job(self, func, *, trigger=None, id=None, replace_existing=False, **kwargs):  # noqa: A002, ARG002
        self.jobs[id] = {"func": func, "trigger": trigger, **kwargs}

    def start(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeMQTT:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, Any]] = []
        self.lifespan_opened = False

    def subscribe(self, topic: str, handler: Any) -> None:
        self.subscriptions.append((topic, handler))

    @asynccontextmanager
    async def lifespan(self):
        self.lifespan_opened = True
        yield
        self.lifespan_opened = False


class FakeStateManager:
    @asynccontextmanager
    async def lifespan(self):
        yield


class FakeContainer:
    """Минимальная копия DI-контейнера: все ресурсы — моки с записью вызовов."""

    def __init__(self) -> None:
        self.wired = False
        self.unwired = False
        self.redis_inst = FakeRedis()
        self.binary_redis_inst = FakeRedis()
        self.twitch_svc = SimpleNamespace(startup=AsyncMock())
        self.chat_bot_svc = SimpleNamespace(
            startup=AsyncMock(), update_bot_channels=AsyncMock(), on_message=AsyncMock()
        )
        self.ai_svc = SimpleNamespace(startup=AsyncMock())
        self.tts_svc = SimpleNamespace(startup=AsyncMock(), shutdown=AsyncMock())
        self.stickers_svc = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        self.mqtt_svc = FakeMQTT()
        self.slovotron_svc = SimpleNamespace(handle_webhook=AsyncMock())
        self.state_manager_svc = FakeStateManager()
        self.cache_svc = object()
        self.statistics_svc = SimpleNamespace(
            flush_to_db=AsyncMock(), cleanup_old_data=AsyncMock(), set_gauge=AsyncMock()
        )
        self.server_metrics_svc = SimpleNamespace(
            tick=AsyncMock(), flush_to_db=AsyncMock(), cleanup_old_data=AsyncMock()
        )
        self.sse_manager_svc = SimpleNamespace(snapshot=AsyncMock(return_value={"total": 0}))
        self.scheduler_svc = FakeScheduler()
        self.memealerts_svc = SimpleNamespace(run_periodic_update=AsyncMock())
        self.token_svc = SimpleNamespace(run_periodic_update=AsyncMock())
        self.clips_svc = SimpleNamespace(run_periodic_poll=AsyncMock())
        self.eventsub_svc = SimpleNamespace(handle_reward_redemption=AsyncMock())
        self._session_factory = object()

    def wire(self) -> None:
        self.wired = True

    def unwire(self) -> None:
        self.unwired = True

    def redis(self):
        return self.redis_inst

    def binary_redis(self):
        return self.binary_redis_inst

    def twitch(self):
        return self.twitch_svc

    def chat_bot(self):
        return self.chat_bot_svc

    def ai(self):
        return self.ai_svc

    def mqtt(self):
        return self.mqtt_svc

    def slovotron(self):
        return self.slovotron_svc

    def state_manager(self):
        return self.state_manager_svc

    def cache(self):
        return self.cache_svc

    def statistics(self):
        return self.statistics_svc

    def server_metrics(self):
        return self.server_metrics_svc

    def sse_manager(self):
        return self.sse_manager_svc

    def scheduler(self):
        return self.scheduler_svc

    def memealerts_auth(self):
        return self.memealerts_svc

    def twitch_token_service(self):
        return self.token_svc

    def clips_poller(self):
        return self.clips_svc

    def stickers_processor(self):
        return self.stickers_svc

    def tts_service(self):
        return self.tts_svc

    def twitch_eventsub_service(self):
        return self.eventsub_svc

    def db_session_factory(self):
        return self._session_factory


@pytest.fixture
def fake_container(monkeypatch):
    container = FakeContainer()
    monkeypatch.setattr("container.Container", lambda: container)
    engine = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(dependencies, "async_engine", engine)
    return container


async def run_lifespan(app=None):
    async with dependencies.lifespan(app):
        pass


class TestLifespanStartup:
    async def test_full_cycle(self, fake_container, monkeypatch):
        monkeypatch.setattr(settings.app, "update_bot_channels_on_startup", True)
        app = SimpleNamespace()

        await run_lifespan(app)

        c = fake_container
        # Fail-fast Redis: keyspace-events + пинги обоих клиентов
        assert c.redis_inst.config_sets["notify-keyspace-events"] == "Egx"
        assert c.redis_inst.closed
        assert c.binary_redis_inst.closed
        # Стартапы сервисов
        c.twitch_svc.startup.assert_awaited_once()
        c.chat_bot_svc.startup.assert_awaited_once_with(c.twitch_svc)
        c.ai_svc.startup.assert_awaited_once()
        c.tts_svc.startup.assert_awaited_once()
        c.stickers_svc.start.assert_awaited_once()
        c.chat_bot_svc.update_bot_channels.assert_awaited_once()
        # MQTT: базовые подписки + telegram-топики
        topics = [t for t, _ in c.mqtt_svc.subscriptions]
        assert MQTTTopic.SLOVOTRON in topics
        assert MQTTTopic.TELEGRAM_CHAT_CONNECTED in topics
        assert MQTTTopic.TELEGRAM_CHAT_DISCONNECTED in topics
        assert MQTTTopic.TELEGRAM_RESULT in topics
        # Прямой режим: чат/награды НЕ через брокер
        assert MQTTTopic.CHAT_MESSAGES not in topics
        assert MQTTTopic.REWARD_REDEMPTIONS not in topics
        # Планировщик: все джобы + shutdown
        assert set(c.scheduler_svc.jobs) == EXPECTED_JOBS
        assert c.scheduler_svc.started
        assert c.scheduler_svc.shutdown_called
        # Shutdown-порядок: tts → stickers → engine → redis → unwire
        c.tts_svc.shutdown.assert_awaited_once()
        c.stickers_svc.stop.assert_awaited_once()
        assert c.wired
        assert c.unwired
        # app.container прокинут, глобальный контейнер сброшен
        assert app.container is c
        import container_runtime as cr

        assert cr._container is None  # set_container(None) в конце

    async def test_update_bot_channels_disabled(self, fake_container, monkeypatch):
        monkeypatch.setattr(settings.app, "update_bot_channels_on_startup", False)
        await run_lifespan()
        fake_container.chat_bot_svc.update_bot_channels.assert_not_awaited()

    async def test_broker_message_mode_subscribes_chat(self, fake_container, monkeypatch):
        monkeypatch.setattr(settings.app, "direct_handle_messages", False)
        await run_lifespan()
        topics = [t for t, _ in fake_container.mqtt_svc.subscriptions]
        assert MQTTTopic.CHAT_MESSAGES in topics

    async def test_broker_reward_mode_subscribes_rewards(self, fake_container, monkeypatch):
        monkeypatch.setattr(settings.app, "direct_handle_rewards", False)
        await run_lifespan()
        topics = [t for t, _ in fake_container.mqtt_svc.subscriptions]
        assert MQTTTopic.REWARD_REDEMPTIONS in topics

    async def test_broker_sending_mode_not_implemented(self, fake_container, monkeypatch):
        import container_runtime as cr

        monkeypatch.setattr(settings.app, "direct_sending_messages", False)
        try:
            with pytest.raises(NotImplementedError):
                await run_lifespan()
        finally:
            cr.set_container(None)


class TestLifespanJobs:
    async def test_snapshot_sse_job_delegates_to_statistics(self, fake_container):
        await run_lifespan()
        job = fake_container.scheduler_svc.jobs["snapshot_sse"]["func"]

        fake_container.sse_manager_svc.snapshot = AsyncMock(return_value={"total": 3, "heat": 1})
        await job()

        fake_container.statistics_svc.set_gauge.assert_called_once()
        args = fake_container.statistics_svc.set_gauge.call_args.args
        assert str(args[0]) == "sse_connections"
        assert args[1] == {"total": 3, "heat": 1}

    async def test_snapshot_sse_job_failure_no_raise(self, fake_container):
        await run_lifespan()
        job = fake_container.scheduler_svc.jobs["snapshot_sse"]["func"]

        fake_container.sse_manager_svc.snapshot = AsyncMock(side_effect=RuntimeError("boom"))
        await job()  # исключение проглочено и залогировано

    async def test_cleanup_overlay_job_wired(self, fake_container, monkeypatch):
        called = {}

        async def fake_cleanup(twitch, sse_manager, cache, factory):  # noqa: ARG001
            called["args"] = (twitch, sse_manager, cache, factory)

        monkeypatch.setattr("services.overlay_eventsub_cleanup.cleanup_overlay_eventsub", fake_cleanup)
        await run_lifespan()

        job = fake_container.scheduler_svc.jobs["cleanup_overlay_eventsub"]["func"]
        await job()

        assert called["args"] == (
            fake_container.twitch_svc,
            fake_container.sse_manager_svc,
            fake_container.cache_svc,
            fake_container._session_factory,
        )

    async def test_cleanup_overlay_job_failure_no_raise(self, fake_container, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("services.overlay_eventsub_cleanup.cleanup_overlay_eventsub", boom)
        await run_lifespan()

        job = fake_container.scheduler_svc.jobs["cleanup_overlay_eventsub"]["func"]
        await job()  # не падает

    async def test_telegram_result_handler_delegates(self, fake_container, monkeypatch):
        sent = {}

        async def fake_handle(payload, factory):  # noqa: ARG001
            sent["payload"] = payload

        monkeypatch.setattr("services.telegram_integration.handle_telegram_result", fake_handle)
        await run_lifespan()

        handlers = dict(fake_container.mqtt_svc.subscriptions)
        await handlers[MQTTTopic.TELEGRAM_RESULT]({"request_id": "x"})

        assert sent["payload"] == {"request_id": "x"}
