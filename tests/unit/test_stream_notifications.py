# ruff: noqa: S101, ARG001, ARG002

"""Юнит-тесты StreamNotificationsService: шаблоны, перезапуск стрима, отложенное окончание."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY

import pytest
from apscheduler.jobstores.base import JobLookupError

from schemas.twitch import StreamOfflineEventSchema, StreamOnlineEventSchema
from services.cache import Cache
from services.stream_notifications import (
    _DEFERRED_OFFLINE_JOB_PREFIX,
    _DEFERRED_OFFLINE_MAX_ATTEMPTS,
    _STREAM_OFFLINE_TEXT,
    _STREAM_RESTART_TEXT,
    StreamNotificationsService,
    process_deferred_stream_offline,
    render_stream_offline_message,
    render_stream_online_message,
    render_stream_restart_message,
)
from tests.unit.fixtures.fake_redis import FakeRedis

USER_ID = 7


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """AsyncSession-эмуляция: execute всегда возвращает один и тот же объект."""

    def __init__(self, value):
        self.value = value
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        self.statements.append(stmt)
        return FakeResult(self.value)

    async def commit(self):
        pass


class FakeScheduler:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.removed: list[str] = []

    def add_job(self, func, *, trigger, run_date, id, replace_existing=False, misfire_grace_time=None, kwargs=None):  # noqa: A002
        self.jobs[id] = {"func": func, "trigger": trigger, "run_date": run_date, "kwargs": kwargs or {}}

    def remove_job(self, id):  # noqa: A002
        if id not in self.jobs:
            raise JobLookupError(id)
        self.removed.append(id)
        del self.jobs[id]


class FakeMQTT:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic, payload):
        self.published.append((topic, payload))


class FakeTwitch:
    def __init__(self, streams=None, error=False):
        self.streams = streams or {}
        self.error = error
        self.calls = 0

    async def get_streams(self, users):
        self.calls += 1
        if self.error:
            raise RuntimeError("helix unavailable")
        return self.streams


class _HashableNS(SimpleNamespace):
    """SimpleNamespace с хэшем по идентичности — можно класть ключом в dict (как User)."""

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other


def make_tg(**overrides) -> SimpleNamespace:
    fields = {
        "stream_notification_enabled": True,
        "stream_chat_id": "100",
        "stream_message_template": None,
        "stream_offline_message_template": None,
        "stream_restart_message_template": None,
        "stream_restart_behavior": None,
        "stream_link_preview_enabled": True,
        "last_stream_message_id": "55",
        "last_stream_offline_message_id": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_user(tg=None, **overrides) -> SimpleNamespace:
    fields = {"id": USER_ID, "login_name": "quantum075", "twitch_id": "200", "telegram": tg}
    fields.update(overrides)
    return _HashableNS(**fields)


def online_payload(user_id=200) -> SimpleNamespace:
    return SimpleNamespace(
        event=StreamOnlineEventSchema(
            id="stream-1",
            broadcaster_user_id=user_id,
            broadcaster_user_login="quantum075",
            broadcaster_user_name="Quantum075",
            type="live",
            started_at=datetime.now(UTC),
        )
    )


def offline_payload(user_id=200) -> SimpleNamespace:
    return SimpleNamespace(
        event=StreamOfflineEventSchema(
            broadcaster_user_id=user_id,
            broadcaster_user_login="quantum075",
            broadcaster_user_name="Quantum075",
        )
    )


def make_service(user, *, twitch=None, mqtt=None, cache=None):
    session = FakeSession(user)
    service = StreamNotificationsService(
        twitch=twitch or FakeTwitch(),
        db_session_factory=lambda: session,
        mqtt=mqtt or FakeMQTT(),
        cache=cache,
    )
    return service, session


@pytest.fixture
def fake_container(monkeypatch):
    """container_runtime.get_container → фейковый контейнер с FakeScheduler."""
    scheduler = FakeScheduler()
    holder = SimpleNamespace(scheduler=lambda: scheduler, stream_notifications=lambda: None)
    monkeypatch.setattr("container_runtime.get_container", lambda: holder)
    holder.scheduler_obj = scheduler
    return holder


@asynccontextmanager
async def fire_and_forget_scope():
    """Дождаться fire-and-forget задач, созданных внутри блока @_task_wrapper-хендлером.

    Session-loop общий для всех тестов (conftest), и в нём могут жить фоновые
    задачи от предыдущих файлов — ждём только задачи, появившиеся после входа
    в блок, иначе зависнем на «чужой» бесконечной задаче.
    """
    me = asyncio.current_task()
    before = {t for t in asyncio.all_tasks() if t is not me}
    yield
    for _ in range(50):
        pending = [t for t in asyncio.all_tasks() if t is not me and t not in before and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


# ----------------------------------------------------------------------
# Шаблоны сообщений (чистые функции)
# ----------------------------------------------------------------------


def test_render_online_default_includes_title_category_link():
    tg = make_tg()
    text = render_stream_online_message(tg, "Quantum075", "Тестовый стрим", "Just Chatting", "https://twitch.tv/q")
    assert text.startswith("🔴 Quantum075 начинает стрим!")
    assert "Тестовый стрим" in text
    assert "Just Chatting" in text
    assert text.endswith("https://twitch.tv/q")


def test_render_online_custom_template():
    tg = make_tg(stream_message_template="{streamer} играет {title} в {category}: {link}")
    text = render_stream_online_message(tg, "Q", "T", "C", "L")
    assert text == "Q играет T в C: L"


def test_render_online_invalid_placeholder_falls_back_to_default():
    tg = make_tg(stream_message_template="{unknown_key}")
    text = render_stream_online_message(tg, "Q", "T", "C", "L")
    assert text.startswith("🔴 Q начинает стрим!")
    assert "L" in text


def test_render_offline_default_text():
    assert render_stream_offline_message(make_tg(), "q", "https://twitch.tv/q") == _STREAM_OFFLINE_TEXT


def test_render_offline_custom_and_invalid_template():
    custom = make_tg(stream_offline_message_template="{streamer} закончил: {link}")
    assert render_stream_offline_message(custom, "Q", "L") == "Q закончил: L"
    broken = make_tg(stream_offline_message_template="{nope}")
    assert render_stream_offline_message(broken, "Q", "L") == _STREAM_OFFLINE_TEXT


def test_render_restart_default_and_custom():
    assert render_stream_restart_message(make_tg(), "Q", "L") == _STREAM_RESTART_TEXT
    custom = make_tg(stream_restart_message_template="{streamer} вернулся: {link}")
    assert render_stream_restart_message(custom, "Q", "L") == "Q вернулся: L"
    broken = make_tg(stream_restart_message_template="{nope}")
    assert render_stream_restart_message(broken, "Q", "L") == _STREAM_RESTART_TEXT


def test_render_online_empty_template_treated_as_default():
    tg = make_tg(stream_message_template="   ")
    text = render_stream_online_message(tg, "Q", "T", "C", "L")
    assert text.startswith("🔴 Q начинает стрим!")


# ----------------------------------------------------------------------
# handle_stream_online
# ----------------------------------------------------------------------


async def test_online_notification_disabled_no_publish(fake_container):
    user = make_user(make_tg(stream_notification_enabled=False))
    service, _ = make_service(user)
    mqtt = service._mqtt

    async with fire_and_forget_scope():
        await service.handle_stream_online(online_payload())

    assert mqtt.published == []


async def test_online_new_stream_publishes_message(fake_container):
    user = make_user(make_tg())
    stream = SimpleNamespace(title="Тестовый стрим", game_name="Just Chatting")
    service, _ = make_service(user, twitch=FakeTwitch(streams={user: stream}))
    mqtt = service._mqtt

    async with fire_and_forget_scope():
        await service.handle_stream_online(online_payload())

    assert len(mqtt.published) == 1
    topic, payload = mqtt.published[0]
    assert topic == "telegram/send_message"
    assert payload["request_id"] == f"stream_online:{USER_ID}"
    assert payload["chat_id"] == "100"
    assert "Тестовый стрим" in payload["message_text"]
    assert "Just Chatting" in payload["message_text"]
    assert "disable_web_page_preview" not in payload  # preview включён


async def test_online_preview_disabled_flag(fake_container):
    user = make_user(make_tg(stream_link_preview_enabled=False))
    service, _ = make_service(user)
    mqtt = service._mqtt

    async with fire_and_forget_scope():
        await service.handle_stream_online(online_payload())

    assert mqtt.published[0][1]["disable_web_page_preview"] is True


async def test_online_quick_restart_silent_cancels_job(fake_container):
    """Быстрый перезапуск с behavior=silent: джоба отменяется, тишина в TG."""
    user = make_user(make_tg(stream_restart_behavior="silent"))
    service, _ = make_service(user)
    mqtt = service._mqtt
    scheduler = fake_container.scheduler_obj
    scheduler.jobs[f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{USER_ID}"] = {"kwargs": {"user_id": USER_ID}}

    async with fire_and_forget_scope():
        await service.handle_stream_online(online_payload())

    assert mqtt.published == []
    assert scheduler.removed == [f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{USER_ID}"]


async def test_online_quick_restart_notify_respects_cooldown(fake_container):
    """behavior=notify: сообщение о рестарте не чаще одного за окно (Redis-CD)."""
    cache = Cache(redis=FakeRedis(), binary_redis=FakeRedis())
    user = make_user(make_tg(stream_restart_behavior="notify"))
    service, _ = make_service(user, cache=cache)
    mqtt = service._mqtt
    scheduler = fake_container.scheduler_obj
    job_id = f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{USER_ID}"

    async def restart():
        scheduler.jobs[job_id] = {"kwargs": {"user_id": USER_ID}}
        async with fire_and_forget_scope():
            await service.handle_stream_online(online_payload())

    await restart()
    assert len(mqtt.published) == 1
    assert mqtt.published[0][1]["message_text"] == _STREAM_RESTART_TEXT

    # Второй рестарт в том же окне — CD активен, не дублируем.
    await restart()
    assert len(mqtt.published) == 1


# ----------------------------------------------------------------------
# handle_stream_offline
# ----------------------------------------------------------------------


async def test_offline_disabled_no_job(fake_container):
    user = make_user(make_tg(stream_chat_id=None))
    service, _ = make_service(user)

    async with fire_and_forget_scope():
        await service.handle_stream_offline(offline_payload())

    assert fake_container.scheduler_obj.jobs == {}


async def test_offline_schedules_deferred_confirmation(fake_container):
    from config import settings

    user = make_user(make_tg())
    service, _ = make_service(user)
    scheduler = fake_container.scheduler_obj

    async with fire_and_forget_scope():
        await service.handle_stream_offline(offline_payload())

    job_id = f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{USER_ID}"
    assert job_id in scheduler.jobs
    job = scheduler.jobs[job_id]
    assert job["kwargs"] == {"user_id": USER_ID}
    expected_delay = timedelta(minutes=settings.app.stream_restart_window_minutes)
    assert datetime.now(UTC) + expected_delay - timedelta(seconds=30) <= job["run_date"]

    # Повторный offline перезапускает таймер, а не плодит джобы.
    async with fire_and_forget_scope():
        await service.handle_stream_offline(offline_payload())
    assert list(scheduler.jobs) == [job_id]


# ----------------------------------------------------------------------
# process_deferred_stream_offline
# ----------------------------------------------------------------------


def _wire_service(fake_container, service):
    fake_container.stream_notifications = lambda: service


async def test_deferred_user_missing_noop(fake_container):
    session = FakeSession(None)
    service = StreamNotificationsService(
        twitch=FakeTwitch(),
        db_session_factory=lambda: session,
        mqtt=FakeMQTT(),
    )
    _wire_service(fake_container, service)

    await process_deferred_stream_offline(USER_ID)

    assert service._mqtt.published == []
    assert session.statements  # только select


async def test_deferred_stream_live_again_no_actions(fake_container):
    user = make_user(make_tg())
    service, session = make_service(user, twitch=FakeTwitch(streams={user: SimpleNamespace(title="t")}))
    _wire_service(fake_container, service)

    await process_deferred_stream_offline(USER_ID)

    assert service._mqtt.published == []
    assert len(session.statements) == 1  # только select, online-id не чистим


async def test_deferred_helix_unknown_retries_then_gives_up(fake_container):
    user = make_user(make_tg())
    service, _ = make_service(user, twitch=FakeTwitch(error=True))
    _wire_service(fake_container, service)
    scheduler = fake_container.scheduler_obj
    job_id = f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{USER_ID}"

    await process_deferred_stream_offline(USER_ID, attempt=1)

    # Перепланировали попытку 2.
    assert job_id in scheduler.jobs
    assert scheduler.jobs[job_id]["kwargs"] == {"user_id": USER_ID, "attempt": 2}

    # Последняя попытка — сдаёмся, не перепланируем.
    scheduler.jobs.clear()
    await process_deferred_stream_offline(USER_ID, attempt=_DEFERRED_OFFLINE_MAX_ATTEMPTS)
    assert scheduler.jobs == {}
    assert service._mqtt.published == []


async def test_deferred_confirmed_offline_delete_behavior(fake_container):
    user = make_user(make_tg(stream_offline_behavior="delete", last_stream_message_id="55"))
    service, session = make_service(user, twitch=FakeTwitch(streams={}))
    _wire_service(fake_container, service)

    await process_deferred_stream_offline(USER_ID)

    expected_delete = ("telegram/delete_message", {"request_id": ANY, "chat_id": "100", "message_id": "55"})
    assert service._mqtt.published == [expected_delete]
    # online-id очищен: после select выполняется update.
    assert len(session.statements) == 2


async def test_deferred_confirmed_offline_message_behavior(fake_container):
    user = make_user(make_tg(stream_offline_behavior="message"))
    service, _ = make_service(user, twitch=FakeTwitch(streams={}))
    _wire_service(fake_container, service)

    await process_deferred_stream_offline(USER_ID)

    topic, payload = service._mqtt.published[0]
    assert topic == "telegram/send_message"
    assert payload["request_id"] == f"stream_offline:{USER_ID}"
    assert payload["message_text"] == _STREAM_OFFLINE_TEXT


async def test_deferred_confirmed_offline_keep_behavior_silent(fake_container):
    user = make_user(make_tg(stream_offline_behavior="keep"))
    service, session = make_service(user, twitch=FakeTwitch(streams={}))
    _wire_service(fake_container, service)

    await process_deferred_stream_offline(USER_ID)

    assert service._mqtt.published == []
    assert len(session.statements) == 2  # select + очистка online-id


async def test_deferred_delete_without_message_id_no_delete_call(fake_container):
    user = make_user(make_tg(stream_offline_behavior="delete", last_stream_message_id=None))
    service, _ = make_service(user, twitch=FakeTwitch(streams={}))
    _wire_service(fake_container, service)

    await process_deferred_stream_offline(USER_ID)

    assert service._mqtt.published == []
