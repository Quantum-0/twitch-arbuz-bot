# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты ChatBot: отправка сообщений, метрики времени обработки, SSE-broadcast,
подписки каналов (update_bot_channels) и диспетчер CommandsManager."""

import asyncio
import json
from collections.abc import AsyncIterator
from time import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import twitch.chat.bot as bot_module
from exceptions import ToManyChatUnsubscribesStartupException, UserNotFoundInDatabase
from schemas.api import StatsType
from schemas.twitch import ChatMessageWebhookEventSchema
from tests.unit.fixtures.fake_redis import SessionCtx
from twitch.chat.base.base_command import Command
from twitch.chat.bot import ChatBot
from twitch.chat.command_manager import CommandsManager
from twitch.state_manager import InMemoryStateManager


def make_event(
    text: str = "hi",
    broadcaster_id: int = 123456,
    broadcaster_login: str = "test_user",
    chatter_id: int = 777,
    chatter_login: str = "chatter",
    reply_parent_name: str | None = None,
    source_broadcaster_id: int | None = None,
    with_emote: bool = False,
) -> ChatMessageWebhookEventSchema:
    event: dict[str, Any] = {
        "broadcaster_user_id": broadcaster_id,
        "broadcaster_user_login": broadcaster_login,
        "broadcaster_user_name": broadcaster_login,
        "chatter_user_id": chatter_id,
        "chatter_user_login": chatter_login,
        "chatter_user_name": chatter_login.capitalize(),
        "message_id": str(uuid4()),
        "message": {"text": text, "fragments": []},
        "color": "#FFFFFF",
        "badges": [],
        "message_type": "text",
    }
    if reply_parent_name is not None:
        event["reply"] = {
            "parent_message_id": "pm-1",
            "parent_message_body": "parent",
            "parent_user_id": "3",
            "parent_user_name": reply_parent_name,
            "parent_user_login": "parent_login",
            "thread_message_id": "tm-1",
            "thread_user_id": "3",
            "thread_user_name": reply_parent_name,
            "thread_user_login": "parent_login",
        }
    if source_broadcaster_id is not None:
        event["source_broadcaster_user_id"] = source_broadcaster_id
        event["source_broadcaster_user_name"] = "Source"
        event["source_broadcaster_user_login"] = "source"
        event["source_message_id"] = str(uuid4())
        event["source_badges"] = []
        event["is_source_only"] = False
    if with_emote:
        event["message"]["fragments"] = [
            {"type": "text", "text": "hi ", "cheermote": None, "emote": None, "mention": None},
            {
                "type": "emote",
                "text": "Kappa",
                "cheermote": None,
                "emote": {"id": "25", "emote_set_id": "0", "owner_id": "1", "format": ["static"]},
                "mention": None,
            },
        ]
    return ChatMessageWebhookEventSchema.model_validate(event)


class FakeStatistics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, str]] = []
        self.channels: list[tuple[str, int]] = []
        self.timings: list[tuple[str, int]] = []

    def inc(self, type_, subtype: str = "", channel_id: int | None = None, amount: int = 1):  # noqa: ARG002
        self.counters.append((str(type_), subtype))

    def inc_timing(self, type_, subtype: str = "", value_ms: int = 0, **kwargs):  # noqa: ARG002
        self.timings.append((str(type_), value_ms))

    def mark_channel(self, subtype: str, channel_id: int) -> None:
        self.channels.append((subtype, channel_id))


class FakeSSEManager:
    def __init__(self, has_clients: bool = True) -> None:
        self._has = has_clients
        self.broadcasts: list[tuple[int, str, Any]] = []

    async def has_clients(self, user_id: int, channel: str) -> bool:  # noqa: ARG002
        return self._has

    async def broadcast(self, user_id: int, channel: str, payload: Any) -> None:
        self.broadcasts.append((user_id, channel, payload))


class FakeUserListManager:
    def __init__(self) -> None:
        self.handled: list[str] = []

    async def handle(self, channel: str, message: Any) -> None:
        self.handled.append(channel)

    def get_active_users(self, channel: str, timeout: float | None = None) -> list[tuple[str, float]]:  # noqa: ARG002
        return [("Alice", time() - 30), ("Bob", time() - 90)]

    def get_last_active(self, channel: str, user: str, timeout: float | None = None) -> float | None:  # noqa: ARG002
        return 123.5


def make_bot(db_session, statistics=None, sse=None, bans=None) -> ChatBot:
    return ChatBot(
        db_session_factory=lambda: SessionCtx(db_session),
        state_manager=InMemoryStateManager(),
        mqtt=SimpleNamespace(),
        statistics=statistics,
        sse_manager=sse,
        bans=bans,
    )


@pytest.fixture
def bot(db_session) -> ChatBot:
    return make_bot(db_session)


@pytest.fixture(autouse=True)
def clean_processing_context():
    bot_module._message_processing_start.set(None)
    bot_module._message_processing_task.set(None)
    yield
    bot_module._message_processing_start.set(None)
    bot_module._message_processing_task.set(None)


class TestSendMessage:
    async def test_empty_message_noop(self, bot):
        bot._twitch = SimpleNamespace(send_chat_message=AsyncMock())
        await bot.send_message(SimpleNamespace(twitch_id="1", login_name="x"), "")
        bot._twitch.send_chat_message.assert_not_awaited()

    async def test_direct_send_via_api_with_stats(self, db_session):
        statistics = FakeStatistics()
        bot = make_bot(db_session, statistics=statistics)
        bot._twitch = SimpleNamespace(send_chat_message=AsyncMock())
        chat_user = SimpleNamespace(twitch_id="123456", login_name="test_user")

        await bot.send_message(chat_user, "hello")

        bot._twitch.send_chat_message.assert_awaited_once_with(
            stream_channel_id="123456",
            stream_channel_login="test_user",
            message="hello",
            reply_parent_message_id=None,
        )
        assert (str(StatsType.MESSAGE_OUTGOING), "") in statistics.counters
        assert ("outgoing", 123456) in statistics.channels

    async def test_timing_recorded_once_per_message(self, db_session):
        statistics = FakeStatistics()
        bot = make_bot(db_session, statistics=statistics)
        bot._twitch = SimpleNamespace(send_chat_message=AsyncMock())

        # Симулируем контекст обработки сообщения этой же задачей
        bot_module._message_processing_start.set(1.0)
        bot_module._message_processing_task.set(id(asyncio.current_task()))

        await bot.send_message(SimpleNamespace(twitch_id="1", login_name="x"), "first")
        await bot.send_message(SimpleNamespace(twitch_id="1", login_name="x"), "second")

        # timing записан ровно один раз — только для первого ответа
        assert len(statistics.timings) == 1
        assert statistics.timings[0][0] == str(StatsType.MESSAGE_PROCESSING_TIME)

    async def test_detached_task_does_not_record_timing(self, db_session):
        statistics = FakeStatistics()
        bot = make_bot(db_session, statistics=statistics)
        bot._twitch = SimpleNamespace(send_chat_message=AsyncMock())

        # start выставлен «другой задачей» — как в detached-тасках (finish_raffle)
        bot_module._message_processing_start.set(1.0)
        bot_module._message_processing_task.set(999999)

        await bot.send_message(SimpleNamespace(twitch_id="1", login_name="x"), "msg")
        assert statistics.timings == []

    async def test_send_message_from_broker(self, bot):
        bot._twitch = SimpleNamespace(send_chat_message=AsyncMock())
        await bot.send_message_from_broker(
            {
                "stream_channel_id": "42",
                "stream_channel_login": "chan",
                "message": "hi",
                "reply_parent_message_id": "pm-1",
                "for_source_only": True,
            }
        )
        bot._twitch.send_chat_message.assert_awaited_once_with(
            stream_channel_id="42",
            stream_channel_login="chan",
            message="hi",
            reply_parent_message_id="pm-1",
            for_source_only=True,
        )


class TestGetUser:
    async def test_found_and_login_sync(self, bot, test_user):
        async with bot._get_user_with_settings_by_twitch_id("123456") as user:
            assert user.login_name == "test_user"
            # login в сообщении отличается — синхронизируется
            assert user.settings is not None

    async def test_login_updated_on_mismatch(self, bot, test_user):
        async with bot._get_user_with_settings_by_twitch_id("123456") as user:
            user.login_name = "new_login"
        async with bot._get_user_with_settings_by_twitch_id("123456") as user:
            assert user.login_name == "new_login"  # commit в __exit__ сохранил

    async def test_not_found_raises(self, bot):
        with pytest.raises(UserNotFoundInDatabase):
            async with bot._get_user_with_settings_by_twitch_id("999"):
                pass


class TestSseBroadcast:
    async def test_broadcast_with_clients(self, db_session):
        sse = FakeSSEManager(has_clients=True)
        bot = make_bot(db_session, sse=sse)
        msg = make_event(text="hi Kappa", with_emote=True, chatter_login="chatter")

        await bot._broadcast_message_to_sse(msg)

        assert len(sse.broadcasts) == 1
        user_id, channel, payload = sse.broadcasts[0]
        assert user_id == 123456
        assert channel == "msg"
        data = json.loads(payload)
        assert data["text"] == "hi Kappa"
        assert data["username"] == "chatter"
        assert data["role"] == "chatter"
        assert data["emotes"] == [{"id": "25", "text": "Kappa"}]

    async def test_no_clients_no_broadcast(self, db_session):
        sse = FakeSSEManager(has_clients=False)
        bot = make_bot(db_session, sse=sse)
        await bot._broadcast_message_to_sse(make_event())
        assert sse.broadcasts == []

    async def test_no_sse_manager_noop(self, bot):
        await bot._broadcast_message_to_sse(make_event())  # не падает

    async def test_bad_broadcaster_id_swallowed(self, db_session):
        sse = FakeSSEManager(has_clients=True)
        bot = make_bot(db_session, sse=sse)
        msg = make_event()
        object.__setattr__(msg, "broadcaster_user_id", "not-a-number")
        await bot._broadcast_message_to_sse(msg)  # TypeError/ValueError проглочены
        assert sse.broadcasts == []


class TestStartup:
    async def test_startup_registers_all(self, bot, monkeypatch, db_session):
        """Стартап регистрирует все хендлеры (7) и команды (29), чат-клиент сохранён."""
        fake_chat = object()
        fake_twitch = SimpleNamespace(build_chat_client=AsyncMock(return_value=fake_chat))
        fake_container = SimpleNamespace(chat_bot=lambda: object(), twitch=lambda: object(), bans=lambda: object())
        monkeypatch.setattr("container_runtime.get_container", lambda: fake_container)

        await bot.startup(fake_twitch)

        assert bot._chat is fake_chat
        assert bot._twitch is fake_twitch
        assert len(bot._handler_manager.handlers) == 7
        assert len(bot._command_manager.commands) == 29
        names = {c.command_name for c in bot._command_manager.commands}
        assert {"pants", "dice", "ban", "unban", "links", "steam", "whoami"} <= names


class TestOnMessageIntegration:
    async def test_dict_input_validated(self, bot, test_user, monkeypatch):
        """on_message принимает и dict, и модель — валидация EventSub-пейлоада."""
        calls = []

        async def _handle(user_settings, streamer, message):
            calls.append("m")

        bot._command_manager = SimpleNamespace(handle=_handle)
        bot._handler_manager = SimpleNamespace(handle=_handle)
        monkeypatch.setattr(bot, "_broadcast_message_to_sse", AsyncMock())

        raw = make_event(text="hello").model_dump(mode="json")
        await bot.on_message(raw)

        assert calls == ["m", "m"]

    async def test_statistics_marked_incoming(self, db_session, test_user, monkeypatch):
        statistics = FakeStatistics()
        bot = make_bot(db_session, statistics=statistics)

        async def _handle(user_settings, streamer, message):
            calls.append("m")

        calls: list[str] = []
        bot._command_manager = SimpleNamespace(handle=_handle)
        bot._handler_manager = SimpleNamespace(handle=_handle)
        monkeypatch.setattr(bot, "_broadcast_message_to_sse", AsyncMock())

        await bot.on_message(make_event())

        assert (str(StatsType.MESSAGE_INCOMING), "") in statistics.counters
        assert ("incoming", 123456) in statistics.channels

    async def test_login_synced_from_message(self, db_session, test_user, monkeypatch):
        bot = make_bot(db_session)
        calls: list[str] = []

        async def _handle(user_settings, streamer, message):
            calls.append(streamer.login_name)

        bot._command_manager = SimpleNamespace(handle=_handle)
        bot._handler_manager = SimpleNamespace(handle=_handle)
        monkeypatch.setattr(bot, "_broadcast_message_to_sse", AsyncMock())

        # Логин в сообщении отличается от БД — синхронизируется до хендлеров
        await bot.on_message(make_event(broadcaster_login="new_login"))

        assert calls == ["new_login", "new_login"]

    async def test_unknown_user_raises(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "_broadcast_message_to_sse", AsyncMock())
        with pytest.raises(UserNotFoundInDatabase):
            await bot.on_message(make_event(broadcaster_id=404, broadcaster_login="ghost"))


class TestDropForBans:
    async def test_no_bans_service(self, bot):
        bot._bans = None
        assert await bot._drop_for_bans(make_event(), "chan") is False

    async def test_bot_role_bypasses(self, db_session, test_user):
        """Сообщения известных ботов не гоняются через бан-сервис."""
        checked = []

        class FakeBans:
            async def get_effective(self, twitch_id):
                checked.append(twitch_id)
                raise AssertionError("не должен вызываться для ботов")

        bot = make_bot(db_session, bans=FakeBans())
        msg = make_event(chatter_login="twibot_bot", chatter_id=111)
        # Бейдж бота
        object.__setattr__(
            msg,
            "badges",
            [type("B", (), {"set_id": "bot", "id": "1", "info": ""})()],
        )
        assert await bot._drop_for_bans(msg, "chan") is False
        assert checked == []

    async def test_banned_chatter_stats_and_sse(self, db_session, test_user):
        statistics = FakeStatistics()
        sse = FakeSSEManager()
        bot = make_bot(db_session, statistics=statistics, sse=sse)

        class FakeBans2:
            async def get_effective(self, twitch_id):  # noqa: ARG002
                from schemas.enums import BanFeature
                from services.bans import EffectiveBans

                return EffectiveBans(global_features=frozenset({BanFeature.CHAT_BOT.value}))

        bot._bans = FakeBans2()
        bot._user_list_manager = FakeUserListManager()
        msg = make_event()
        dropped = await bot._drop_for_bans(msg, "test_user")

        assert dropped is True
        assert (str(StatsType.BAN_BLOCKED), "chat_bot") in statistics.counters
        assert len(sse.broadcasts) == 1
        assert bot._user_list_manager.handled == ["test_user"]


class TestSendMessageModes:
    async def test_broker_mode_no_api_call(self, db_session, monkeypatch):
        """В режиме брокера отправка не ходит в Twitch API, но метрики пишутся."""
        from config import settings

        monkeypatch.setattr(settings.app, "direct_sending_messages", False)
        statistics = FakeStatistics()
        bot = make_bot(db_session, statistics=statistics)
        bot._twitch = SimpleNamespace(send_chat_message=AsyncMock())

        await bot.send_message(SimpleNamespace(twitch_id="123", login_name="x"), "msg")

        bot._twitch.send_chat_message.assert_not_awaited()
        assert (str(StatsType.MESSAGE_OUTGOING), "") in statistics.counters


class TestGetCommands:
    async def test_delegates_to_manager(self, bot, monkeypatch):
        expected = [("dice", "!dice", "кубик")]

        async def fake_get(user):
            assert user.login_name == "chan"
            return expected

        bot._command_manager = SimpleNamespace(get_commands_of_user=fake_get)
        result = await bot.get_commands(SimpleNamespace(login_name="chan"))
        assert result == expected


class TestUserListDelegation:
    async def test_get_last_active_users_formats_delay(self, bot):
        bot._user_list_manager = FakeUserListManager()
        result = await bot.get_last_active_users(SimpleNamespace(login_name="chan"))
        assert len(result) == 2
        name, delay = result[0]
        assert name == "Alice"
        assert "назад" in delay

    async def test_get_last_active_users_accepts_str(self, bot):
        bot._user_list_manager = FakeUserListManager()
        result = await bot.get_last_active_users("chan")
        assert result[0][0] == "Alice"

    async def test_get_user_last_active(self, bot):
        bot._user_list_manager = FakeUserListManager()
        assert await bot.get_user_last_active("chan", "alice") == 123.5

    async def test_get_random_active_user(self, bot, monkeypatch):
        bot._user_list_manager = FakeUserListManager()
        monkeypatch.setattr("twitch.chat.bot.random.choice", lambda seq: seq[-1])
        assert await bot.get_random_active_user("chan") == "Bob"


class TestUpdateBotChannels:
    @staticmethod
    def make_twitch(
        subs: list[dict[str, Any]],
        subscribe_results: list[tuple[Any, bool, dict[str, Any]]] | None = None,
    ) -> SimpleNamespace:
        async def get_subscriptions() -> list[SimpleNamespace]:
            return [SimpleNamespace(**s) for s in subs]

        async def subscribe_chat_messages(*users: Any) -> AsyncIterator[tuple[Any, bool, dict[str, Any]]]:
            results = subscribe_results if subscribe_results is not None else [(u, True, {}) for u in users]
            for item in results:
                yield item

        return SimpleNamespace(
            get_subscriptions=get_subscriptions,
            subscribe_chat_messages=subscribe_chat_messages,
            unsubscribe_event_sub=AsyncMock(),
        )

    async def test_subscribe_new_and_unsubscribe_stale(self, bot, db_session, test_user):
        test_user.settings.enable_chat_bot = True
        await db_session.commit()

        stale_sub = {"id": "sub-1", "type": "channel.chat.message", "condition": {"broadcaster_user_id": "999"}}
        other_sub = {"id": "sub-2", "type": "stream.online", "condition": {"broadcaster_user_id": "123456"}}
        bot._twitch = self.make_twitch(subs=[stale_sub, other_sub])

        await bot.update_bot_channels()

        # Отписались только от ненужного channel.chat.message
        bot._twitch.unsubscribe_event_sub.assert_awaited_once_with("sub-1")

    async def test_authorization_failure_disables_bot(self, bot, db_session, test_user):
        test_user.settings.enable_chat_bot = True
        await db_session.commit()

        bot._twitch = self.make_twitch(
            subs=[],
            subscribe_results=[(test_user, False, {"message": "subscription missing proper authorization"})],
        )

        await bot.update_bot_channels()

        await db_session.refresh(test_user.settings)
        assert test_user.settings.enable_chat_bot is False

    async def test_too_many_unsubscribes_guard(self, bot, test_user, monkeypatch):  # noqa: ARG001
        from config import settings

        monkeypatch.setattr(settings.app, "exception_to_many_unsubscribes", 1)
        # 3 текущих подписки, 0 желаемых → 3 > 1 → исключение
        subs = [
            {"id": f"s{i}", "type": "channel.chat.message", "condition": {"broadcaster_user_id": str(100 + i)}}
            for i in range(3)
        ]
        bot._twitch = self.make_twitch(subs=subs)

        with pytest.raises(ToManyChatUnsubscribesStartupException):
            await bot.update_bot_channels()


class EchoCommand(Command):
    command_name = "echo"
    command_aliases = ["echo", "эхо"]
    command_description = "повторяет"

    def __init__(self, sm, send, db) -> None:  # noqa: ARG002
        super().__init__(sm, send, db)
        self.handled: list[str] = []

    def is_enabled(self, streamer_settings: Any) -> bool:
        return getattr(streamer_settings, "echo_enabled", True)

    async def handle(self, streamer: Any, message: ChatMessageWebhookEventSchema):
        self.handled.append(message.message.text)


@pytest.fixture
def manager(monkeypatch) -> CommandsManager:
    monkeypatch.setattr("container_runtime.get_container", lambda: SimpleNamespace(chat_bot=lambda: object()))
    sm = InMemoryStateManager()

    async def send(chat, message):  # noqa: ARG001
        pass

    mgr = CommandsManager(sm, send, None)
    mgr.register(EchoCommand)
    return mgr


class TestCommandsManager:
    async def test_register_instantiates(self, manager):
        assert len(manager.commands) == 1
        assert isinstance(manager.commands[0], EchoCommand)

    @pytest.mark.parametrize(
        ("text", "should_handle"),
        [
            ("!echo hi", True),
            ("!echo", True),
            ("!эхо привет", True),
            ("!ECHO hi", True),  # lower()
            ("echo hi", False),  # без !
            ("!echoing", False),  # префикс чужой команды
            ("!other", False),
            ("просто текст", False),
        ],
    )
    async def test_dispatch(self, manager, text, should_handle):
        settings_stub = SimpleNamespace(echo_enabled=True, allow_shared_chat=True)
        await manager.handle(settings_stub, SimpleNamespace(), make_event(text=text))
        assert (len(manager.commands[0].handled) == 1) is should_handle

    async def test_disabled_command_skipped(self, manager):
        settings_stub = SimpleNamespace(echo_enabled=False, allow_shared_chat=True)
        await manager.handle(settings_stub, SimpleNamespace(), make_event(text="!echo hi"))
        assert manager.commands[0].handled == []

    async def test_shared_chat_skipped(self, manager):
        settings_stub = SimpleNamespace(echo_enabled=True, allow_shared_chat=False)
        msg = make_event(text="!echo hi", source_broadcaster_id=555)
        await manager.handle(settings_stub, SimpleNamespace(), msg)
        assert manager.commands[0].handled == []

    async def test_reply_mention_stripped(self, manager):
        settings_stub = SimpleNamespace(echo_enabled=True, allow_shared_chat=True)
        msg = make_event(text="@Alice !echo hi", reply_parent_name="Alice")
        await manager.handle(settings_stub, SimpleNamespace(), msg)
        # @Alice вырезан из текста до матчинга алиасов
        assert manager.commands[0].handled == ["!echo hi"]

    async def test_statistics_inc_per_command(self):
        statistics = FakeStatistics()

        async def send(chat, message):  # noqa: ARG001
            pass

        mgr = CommandsManager(InMemoryStateManager(), send, None, statistics=statistics)

        class CountedCommand(EchoCommand):
            def __init__(self, sm, send_, db):
                super().__init__(sm, send_, db)

        import container_runtime

        orig = container_runtime.get_container
        container_runtime._container = SimpleNamespace(chat_bot=lambda: object())
        try:
            mgr.register(CountedCommand)
        finally:
            container_runtime._container = None
            del orig

        await mgr.handle(SimpleNamespace(allow_shared_chat=True), SimpleNamespace(), make_event(text="!echo x"))
        assert (str(StatsType.COMMAND_HANDLED), "echo") in statistics.counters

    async def test_get_commands_of_user(self, manager):
        settings_stub = SimpleNamespace(echo_enabled=True, allow_shared_chat=True)
        user = SimpleNamespace(settings=settings_stub)
        result = await manager.get_commands_of_user(user)
        assert result == [("echo", "!echo, !эхо", "повторяет")]
