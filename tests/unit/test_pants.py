# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты розыгрыша трусов: PantsCommand (старт/кулдауны/finish_raffle) + PantsRaffleHandler."""

import asyncio
import uuid
from time import time
from types import SimpleNamespace
from typing import Any

import pytest

from database.models import PantsDeny, TwitchUserSettings
from schemas.twitch import ChatMessageWebhookEventSchema
from tests.unit.fixtures.fake_redis import SessionCtx
from twitch.chat.commands.pants import PantsCommand
from twitch.chat.handlers.handlers import HandlerResult
from twitch.chat.handlers.pants_raffle import PantsRaffleHandler
from twitch.state_manager import InMemoryStateManager, SMParam

CHANNEL = "streamer"


class FakeChatBot:
    def __init__(self, active: list[tuple[str, str]]) -> None:
        self._active = list(active)
        self._user_list_manager = SimpleNamespace(get_active_users=self._get_active_users)

    async def _get_active_users(self, channel: str, timeout: float | None = None) -> list[tuple[str, str]]:  # noqa: ARG002
        return self._active

    async def get_last_active_users(self, user: Any) -> list[tuple[str, str]]:
        return self._active

    async def get_random_active_user(self, channel: Any, max_period_sec: float = 1800) -> str:  # noqa: ARG002
        return self._active[0][0]


@pytest.fixture
def sm() -> InMemoryStateManager:
    return InMemoryStateManager()


@pytest.fixture
def send_message_mock():
    class SendMessageMock:
        def __init__(self):
            self.calls: list[tuple[Any, str]] = []

        async def __call__(self, chat: Any, message: str) -> None:
            self.calls.append((chat, message))

        def messages(self) -> list[str]:
            return [m for _, m in self.calls]

        def last(self) -> str:
            return self.calls[-1][1]

    return SendMessageMock()


@pytest.fixture(autouse=True)
def no_background_tasks(monkeypatch):
    """Перехватываем отложенный finish_raffle, чтобы 60-секундная задача не утекала в общий loop."""
    captured: list[float] = []

    async def fake_delay(timer: float, coro: Any) -> None:
        captured.append(timer)
        coro.close()

    monkeypatch.setattr("twitch.chat.commands.pants.call_with_delay", fake_delay)
    monkeypatch.setattr("twitch.chat.commands.pants.run_in_clean_otel_context", lambda coro: coro)
    return captured


@pytest.fixture
def fast_sleep(monkeypatch):
    async def _fast(delay: float) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast)


@pytest.fixture
def make_command(sm, send_message_mock, db_session, monkeypatch):
    def _make(active: list[tuple[str, str]] | None = None) -> tuple[PantsCommand, FakeChatBot]:
        bot = FakeChatBot(active if active is not None else [("User1", "u1"), ("User2", "u2"), ("User3", "u3")])
        holder = SimpleNamespace(chat_bot=lambda: bot)
        monkeypatch.setattr("container_runtime.get_container", lambda: holder)
        cmd = PantsCommand(sm, send_message_mock, lambda: SessionCtx(db_session))
        return cmd, bot

    return _make


@pytest.fixture
def streamer() -> SimpleNamespace:
    return SimpleNamespace(login_name="Streamer")


def make_event(text: str, chatter_name: str = "Chatter", chatter_login: str = "chatter", chatter_id: int = 77):
    return ChatMessageWebhookEventSchema.model_validate(
        {
            "broadcaster_user_id": 1,
            "broadcaster_user_login": CHANNEL,
            "broadcaster_user_name": "Streamer",
            "chatter_user_id": chatter_id,
            "chatter_user_login": chatter_login,
            "chatter_user_name": chatter_name,
            "message_id": str(uuid.uuid4()),
            "message": {"text": text, "fragments": []},
            "color": "#FFFFFF",
            "badges": [],
            "message_type": "text",
        }
    )


async def get_target_cd(sm, target: str) -> float | None:
    return await sm.get_state(channel=CHANNEL, command="pants", user=target, param=SMParam.TARGET_COOLDOWN)


class TestHandleStart:
    async def test_active_raffle_blocks_new_one(self, make_command, sm, send_message_mock, streamer):
        cmd, _ = make_command()
        await sm.set_state("Lucky", channel=CHANNEL, command="pants", param=SMParam.USER)

        response = await cmd._handle(streamer, "Chatter", "!трусы")

        assert response == "Невозможно начать новый розыгрыш трусов, пока не разыграли трусы @Lucky"
        assert send_message_mock.calls == []

    async def test_explicit_target_success(self, make_command, sm, send_message_mock, streamer, no_background_tasks):
        cmd, _ = make_command()

        response = await cmd._handle(streamer, "Chatter", "!трусы @User2")

        assert "розыгрыш трусов @User2" in response
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) == "User2"
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS) == set()
        assert await get_target_cd(sm, "User2") is not None
        await asyncio.sleep(0)  # даём созданной задаче fake_delay выполниться
        assert no_background_tasks == [60]

    async def test_explicit_target_case_insensitive(self, make_command, sm, streamer, no_background_tasks):
        cmd, _ = make_command()
        response = await cmd._handle(streamer, "Chatter", "!трусы @user2")
        # Регистр цели сохраняется как в сообщении, проверка active — регистронезависимая
        assert "розыгрыш трусов @user2" in response

    async def test_multiple_targets_rejected(self, make_command, sm, streamer):
        cmd, _ = make_command()
        response = await cmd._handle(streamer, "Chatter", "!трусы @User2 @User3")
        assert "только одну цель" in response
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) is None

    async def test_unknown_target_rejected(self, make_command, sm, streamer):
        cmd, _ = make_command()
        response = await cmd._handle(streamer, "Chatter", "!трусы @Ghost")
        assert "Не вижу такого пользователя" in response

    async def test_denied_target_rejected(self, make_command, sm, db_session, streamer):
        cmd, _ = make_command()
        db_session.add(PantsDeny(name="user2"))
        await db_session.flush()

        response = await cmd._handle(streamer, "Chatter", "!трусы @User2")
        assert "запретил разыгрывать свои трусы" in response
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) is None

    async def test_target_on_cooldown_rejected(self, make_command, sm, streamer):
        cmd, _ = make_command()
        await sm.set_state(time() - 10, channel=CHANNEL, command="pants", user="User2", param=SMParam.TARGET_COOLDOWN)

        response = await cmd._handle(streamer, "Chatter", "!трусы @User2")
        assert "уже были недавно разыграны" in response


class TestRandomTarget:
    async def test_no_active_users(self, make_command, sm, streamer):
        cmd, _ = make_command(active=[])
        response = await cmd._handle(streamer, "Chatter", "!трусы")
        assert "нет пользователей, чьи трусы можно было бы разыграть" in response

    async def test_all_on_cooldown_reports_min_wait(self, make_command, sm, streamer):
        cmd, _ = make_command()
        now = time()
        await sm.set_state(now - 10, channel=CHANNEL, command="pants", user="User1", param=SMParam.TARGET_COOLDOWN)
        await sm.set_state(now - 100, channel=CHANNEL, command="pants", user="User2", param=SMParam.TARGET_COOLDOWN)
        await sm.set_state(now - 50, channel=CHANNEL, command="pants", user="User3", param=SMParam.TARGET_COOLDOWN)

        response = await cmd._handle(streamer, "Chatter", "!трусы")

        # Минимальный ts у User2 — её и назовём
        assert "Трусы @User2 разыграть можно будет через" in response
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) is None

    async def test_denied_user_excluded_from_random(self, make_command, sm, db_session, monkeypatch, streamer):
        cmd, _ = make_command()
        db_session.add(PantsDeny(name="user1"))
        await db_session.flush()
        monkeypatch.setattr("twitch.chat.commands.pants.random.choice", lambda seq: seq[0])

        response = await cmd._handle(streamer, "Chatter", "!трусы")

        # User1 в deny-листе, второй по порядку — User2
        assert "розыгрыш трусов @User2" in response

    async def test_random_success(self, make_command, sm, monkeypatch, streamer, no_background_tasks):
        cmd, _ = make_command()
        monkeypatch.setattr("twitch.chat.commands.pants.random.choice", lambda seq: seq[0])

        response = await cmd._handle(streamer, "Chatter", "!трусы")

        assert "розыгрыш трусов @User1" in response
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) == "User1"
        await asyncio.sleep(0)
        assert no_background_tasks == [60]


class TestCooldowns:
    async def test_per_user_cooldown_silent(self, make_command, sm, send_message_mock, streamer):
        cmd, _ = make_command()
        await sm.set_state("Lucky", channel=CHANNEL, command="pants", param=SMParam.USER)

        await cmd.handle(streamer, make_event("!трусы", chatter_id=1, chatter_login="a", chatter_name="A"))
        await cmd.handle(streamer, make_event("!трусы", chatter_id=1, chatter_login="a", chatter_name="A"))

        # Второй вызов того же юзера в пределах 5с — молчаливый "" из _cooldown_reply
        assert send_message_mock.last() == ""
        assert len(send_message_mock.calls) == 2

    async def test_per_chat_cooldown_silent(self, make_command, sm, send_message_mock, streamer):
        cmd, _ = make_command()
        await sm.set_state("Lucky", channel=CHANNEL, command="pants", param=SMParam.USER)

        await cmd.handle(streamer, make_event("!трусы", chatter_id=1, chatter_login="a", chatter_name="A"))
        await cmd.handle(streamer, make_event("!трусы", chatter_id=2, chatter_login="b", chatter_name="B"))

        assert send_message_mock.last() == ""

    @pytest.mark.parametrize(("flag", "expected"), [(True, True), (False, False)])
    def test_is_enabled(self, make_command, flag, expected):
        cmd, _ = make_command()
        assert cmd.is_enabled(TwitchUserSettings(enable_pants=flag)) is expected


class TestFinishRaffle:
    async def test_no_state_no_messages(self, make_command, send_message_mock, streamer):
        cmd, _ = make_command()
        await cmd.finish_raffle(streamer, "User2")
        assert send_message_mock.calls == []

    async def test_target_mismatch_cancels(self, make_command, sm, send_message_mock, streamer):
        cmd, _ = make_command()
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state({"A", "B"}, channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)

        await cmd.finish_raffle(streamer, "SomeoneElse")

        assert send_message_mock.calls == []
        # Состояния не тронуты — их подчистит finish_raffle настоящего таргета
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) == "User2"

    async def test_no_participants(self, make_command, sm, send_message_mock, streamer):
        cmd, _ = make_command()
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state(set(), channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)

        await cmd.finish_raffle(streamer, "User2")

        assert "никто не принял участие" in send_message_mock.last()
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) is None
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS) is None

    async def test_winner_is_target(self, make_command, sm, send_message_mock, streamer, fast_sleep):
        cmd, _ = make_command()
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state({"User2"}, channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)

        await cmd.finish_raffle(streamer, "User2")

        messages = send_message_mock.messages()
        assert any("целый 1 человек" in m for m in messages)
        assert any("обладателем собственных трусов" in m for m in messages)
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) is None

    async def test_winner_gets_pants(self, make_command, sm, send_message_mock, streamer, fast_sleep, monkeypatch):
        cmd, _ = make_command()
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state({"Alice", "Bob"}, channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)
        monkeypatch.setattr("twitch.chat.commands.pants.random.choice", lambda seq: seq[0])

        await cmd.finish_raffle(streamer, "User2")

        messages = send_message_mock.messages()
        assert any("целых 2 человека" in m for m in messages)
        winner = next(m.split("!")[0].lstrip("@") for m in messages if "счастливым обладателем трусов" in m)
        assert winner in {"Alice", "Bob"}
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS) is None

    async def test_five_participants_plural(
        self, make_command, sm, send_message_mock, streamer, fast_sleep, monkeypatch
    ):
        cmd, _ = make_command()
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state({"A", "B", "C", "D", "E"}, channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)
        monkeypatch.setattr("twitch.chat.commands.pants.random.choice", lambda seq: seq[0])

        await cmd.finish_raffle(streamer, "User2")

        assert any("целых 5 человек" in m for m in send_message_mock.messages())


class TestRaffleHandler:
    @staticmethod
    def make_handler(sm, send_message_mock, db_session) -> PantsRaffleHandler:
        return PantsRaffleHandler(sm, send_message_mock, lambda: SessionCtx(db_session))

    async def test_plus_without_raffle_skipped(self, sm, send_message_mock, db_session, streamer):
        handler = self.make_handler(sm, send_message_mock, db_session)
        assert await handler.handle(streamer, make_event("+")) is HandlerResult.SKIPED

    async def test_plus_adds_participant(self, sm, send_message_mock, db_session, streamer):
        handler = self.make_handler(sm, send_message_mock, db_session)
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state(set(), channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)

        result = await handler.handle(streamer, make_event("+", chatter_name="Alice", chatter_login="alice"))

        assert result is HandlerResult.HANDLED
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS) == {"Alice"}

    async def test_duplicate_plus_skipped(self, sm, send_message_mock, db_session, streamer):
        handler = self.make_handler(sm, send_message_mock, db_session)
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state({"Alice"}, channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)

        result = await handler.handle(streamer, make_event("+", chatter_name="Alice", chatter_login="alice"))

        assert result is HandlerResult.SKIPED
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS) == {"Alice"}

    async def test_fifth_participant_special_message(self, sm, send_message_mock, db_session, streamer):
        handler = self.make_handler(sm, send_message_mock, db_session)
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state({"A", "B", "C", "D"}, channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)

        await handler.handle(streamer, make_event("+", chatter_name="E", chatter_login="e"))

        assert any("целых 5 человек" in m for m in send_message_mock.messages())

    async def test_minus_from_target_cancels_raffle(self, sm, send_message_mock, db_session, streamer):
        handler = self.make_handler(sm, send_message_mock, db_session)
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state({"A"}, channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)

        result = await handler.handle(streamer, make_event("-", chatter_name="User2", chatter_login="user2"))

        assert result is HandlerResult.HANDLED
        assert "розыгрыш отменяется" in send_message_mock.last()
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) is None
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS) is None

    async def test_minus_from_other_skipped(self, sm, send_message_mock, db_session, streamer):
        handler = self.make_handler(sm, send_message_mock, db_session)
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state({"A"}, channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)

        result = await handler.handle(streamer, make_event("-", chatter_name="Alice", chatter_login="alice"))

        assert result is HandlerResult.SKIPED
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.USER) == "User2"

    async def test_denied_chatter_plus_not_added(self, sm, send_message_mock, db_session, streamer):
        handler = self.make_handler(sm, send_message_mock, db_session)
        db_session.add(PantsDeny(name="alice"))
        await db_session.flush()
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)
        await sm.set_state(set(), channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS)

        result = await handler.handle(streamer, make_event("+", chatter_name="Alice", chatter_login="alice"))

        assert result is HandlerResult.HANDLED
        assert await sm.get_state(channel=CHANNEL, command="pants", param=SMParam.PARTICIPANTS) == set()

    async def test_plus_without_participants_state_skipped(self, sm, send_message_mock, db_session, streamer):
        handler = self.make_handler(sm, send_message_mock, db_session)
        await sm.set_state("User2", channel=CHANNEL, command="pants", param=SMParam.USER)

        result = await handler.handle(streamer, make_event("+", chatter_name="Alice", chatter_login="alice"))

        assert result is HandlerResult.SKIPED


class TestPantsDenyDb:
    async def test_check_denied_lowercases(self, make_command, db_session):
        cmd, _ = make_command()
        db_session.add(PantsDeny(name="user2"))
        await db_session.flush()

        denied = await cmd.check_denied("USER2", "User3")
        assert [d.name for d in denied] == ["user2"]

    async def test_check_denied_empty(self, make_command):
        cmd, _ = make_command()
        assert await cmd.check_denied("Nobody") == []
