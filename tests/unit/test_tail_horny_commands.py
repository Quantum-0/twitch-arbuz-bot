# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты команд tail / horny_good / horny_bad (SavingResultCommand-подклассы)."""

import random
import uuid
from time import time
from types import SimpleNamespace
from typing import Any

import pytest

from schemas.twitch import ChatMessageWebhookEventSchema
from tests.unit.fixtures.fake_redis import SessionCtx
from twitch.chat.commands.horny_bad import HornyBadCommand
from twitch.chat.commands.horny_good import HornyGoodCommand
from twitch.chat.commands.tail import TailCommand
from twitch.state_manager import InMemoryStateManager, SMParam

CHANNEL = "streamer"


class FakeChatBot:
    def __init__(self) -> None:
        self._user_list_manager = SimpleNamespace(get_active_users=self._get_active_users)

    async def _get_active_users(self, channel: str, timeout: float | None = None) -> list[tuple[str, float]]:  # noqa: ARG002
        return [("User1", time()), ("User2", time())]

    async def get_random_active_user(self, channel: Any, max_period_sec: float = 1800) -> str:  # noqa: ARG002
        return "RandomUser"


@pytest.fixture
def sm() -> InMemoryStateManager:
    return InMemoryStateManager()


@pytest.fixture
def send_mock():
    class SendMessageMock:
        def __init__(self):
            self.calls: list[tuple[Any, Any]] = []

        async def __call__(self, chat: Any, message: str | None) -> None:
            self.calls.append((chat, message))

        def last(self) -> str | None:
            return self.calls[-1][1]

    return SendMessageMock()


@pytest.fixture
def make_cmd(sm, send_mock, db_session, monkeypatch):
    def _make(cls: type) -> Any:
        bot = FakeChatBot()
        holder = SimpleNamespace(chat_bot=lambda: bot)
        monkeypatch.setattr("container_runtime.get_container", lambda: holder)
        return cls(sm, send_mock, lambda: SessionCtx(db_session))

    return _make


@pytest.fixture
def streamer() -> SimpleNamespace:
    return SimpleNamespace(login_name="Streamer")


def make_event(text: str, chatter_name: str = "Chatter", chatter_id: int = 77):
    return ChatMessageWebhookEventSchema.model_validate(
        {
            "broadcaster_user_id": 1,
            "broadcaster_user_login": CHANNEL,
            "broadcaster_user_name": "Streamer",
            "chatter_user_id": chatter_id,
            "chatter_user_login": chatter_name.lower(),
            "chatter_user_name": chatter_name,
            "message_id": str(uuid.uuid4()),
            "message": {"text": text, "fragments": []},
            "color": "#FFFFFF",
            "badges": [],
            "message_type": "text",
        }
    )


# ═══════════════════════════════════ TAIL ═══════════════════════════════════


class TestTailResultGenerator:
    async def test_first_value(self, make_cmd, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        # f(0.5) = int(0.5*775 + (0.5*65)^2) = int(1443.75) = 1443
        assert await cmd.result_generator(None) == "1443"

    async def test_growth_gets_plus(self, make_cmd, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        assert await cmd.result_generator("1000") == "+1443"

    async def test_shrink_gets_minus(self, make_cmd, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        assert await cmd.result_generator("2000") == "-1443"

    async def test_old_value_with_sign_prefix(self, make_cmd, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        assert await cmd.result_generator("+1000") == "+1443"

    async def test_equal_value_no_sign(self, make_cmd, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        assert await cmd.result_generator("1443") == "1443"


class TestTailConvert:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (5, "5 мм"),
            (45, "45 мм"),
            (40, "4 см"),
            (105, "10.5 см"),
            (700, "70 см"),
            (750, "0.7 м"),
            (1000, "1 м"),
            (1500, "1.5 м"),
            (5000, "5 м"),
        ],
    )
    def test_units(self, make_cmd, value, expected):
        assert make_cmd(TailCommand).convert_tail(value) == expected


class TestTailHandleNew:
    @pytest.mark.parametrize(
        ("new_value", "random_val", "part"),
        [
            ("1", 0.9, "НЕТ ХВОСТА"),  # фиксированный баг: раньше перезаписывалось веткой <10
            ("50", 0.9, "длина твоего хвоста - 5 см"),
            ("+50", 0.9, "Подрос"),
            ("-50", 0.9, "Укоротился"),
            ("50", 0.1, "Коротенький"),
            ("150", 0.1, "Маленький"),
            ("300", 0.1, "Нормальный такой"),
            ("700", 0.1, "Хороооший"),
            ("1500", 0.1, "хвостище"),
            ("3000", 0.1, "гигант"),
        ],
    )
    async def test_buckets(self, make_cmd, streamer, monkeypatch, new_value, random_val, part):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: random_val)
        result = await cmd._handle_new(streamer, "Chatter", "tail", new_value)
        assert part in result


class TestTailHandleOld:
    async def test_mentions_value(self, make_cmd, streamer):
        cmd = make_cmd(TailCommand)
        result = await cmd._handle_old(streamer, "Chatter", "tail", "40", "30")
        assert "4 см" in result
        assert "Chatter" in result

    async def test_strips_sign_prefix(self, make_cmd, streamer):
        cmd = make_cmd(TailCommand)
        result = await cmd._handle_old(streamer, "Chatter", "tail", "+40", "30")
        assert "4 см" in result


class TestTailMisc:
    async def test_cooldown_reply(self, make_cmd):
        result = await make_cmd(TailCommand)._cooldown_reply("Chatter", 30)
        assert "рано измерять" in result

    async def test_target_selected_none(self, make_cmd):
        assert await make_cmd(TailCommand)._target_selected("u", ["@t"]) is None

    def test_is_enabled(self, make_cmd):
        cmd = make_cmd(TailCommand)
        assert cmd.is_enabled(SimpleNamespace(enable_tail=True)) is True
        assert cmd.is_enabled(SimpleNamespace(enable_tail=False)) is False


class TestTailHandle:
    async def test_first_call_stores_value(self, make_cmd, sm, send_mock, streamer, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        await cmd.handle(streamer, make_event("tail"))
        assert "1.4 м" in send_mock.last()
        stored = await sm.get_state(user=77, command="tail", param=SMParam.PREVIOUS_VALUE)
        assert stored == "1443"

    async def test_immediate_second_call_cooldown(self, make_cmd, send_mock, streamer, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        await cmd.handle(streamer, make_event("tail"))
        await cmd.handle(streamer, make_event("tail"))
        assert "рано измерять" in send_mock.last()

    async def test_recent_value_uses_handle_old(self, make_cmd, sm, send_mock, streamer, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        now = time()
        await sm.set_state(channel=CHANNEL, user=77, command="tail", param=SMParam.COOLDOWN, value=now - 100)
        await sm.set_state(user=77, command="tail", param=SMParam.PREVIOUS_VALUE, value="400")
        await sm.set_state(user=77, command="tail", param=SMParam.PREVIOUS_VALUE_TIME, value=now - 60)
        await cmd.handle(streamer, make_event("tail"))
        assert "40 см" in send_mock.last()

    async def test_stale_value_regenerates(self, make_cmd, sm, send_mock, streamer, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        now = time()
        await sm.set_state(channel=CHANNEL, user=77, command="tail", param=SMParam.COOLDOWN, value=now - 100)
        await sm.set_state(user=77, command="tail", param=SMParam.PREVIOUS_VALUE, value="400")
        await sm.set_state(user=77, command="tail", param=SMParam.PREVIOUS_VALUE_TIME, value=now - 700)
        await cmd.handle(streamer, make_event("tail"))
        assert "1.4 м" in send_mock.last()

    async def test_target_message_short_circuits(self, make_cmd, sm, send_mock, streamer, monkeypatch):
        cmd = make_cmd(TailCommand)
        monkeypatch.setattr(random, "random", lambda: 0.5)
        await cmd.handle(streamer, make_event("tail @User2"))
        # _target_selected у tail возвращает None — отправляется None
        assert send_mock.last() is None
        # Кулдаун не тратится
        assert await _get_cd(sm, 77) is None


async def _get_cd(sm, user: int) -> float | None:
    return await sm.get_state(channel=CHANNEL, user=user, command="tail", param=SMParam.COOLDOWN)


# ═════════════════════════════ HORNY GOOD ═══════════════════════════════════


class TestHornyGoodResultGenerator:
    async def test_first_value(self, make_cmd, monkeypatch):
        cmd = make_cmd(HornyGoodCommand)
        monkeypatch.setattr(random, "randint", lambda a, b: 42)
        assert await cmd.result_generator(None) == "42"

    async def test_signs(self, make_cmd, monkeypatch):
        cmd = make_cmd(HornyGoodCommand)
        monkeypatch.setattr(random, "randint", lambda a, b: 42)
        assert await cmd.result_generator("10") == "+42"
        assert await cmd.result_generator("+10") == "+42"
        assert await cmd.result_generator("90") == "-42"
        assert await cmd.result_generator("42") == "42"


class TestHornyGoodHandleNew:
    @pytest.mark.parametrize(
        ("value", "random_val", "part"),
        [
            (10, 0.9, "хорни всего на 10%"),
            (10, 0.1, "хорни всего-лишь на 10%"),
            ("+30", 0.1, "стремительно растёт"),
            (50, 0.1, "Золотая середина"),
            (70, 0.1, "жарко"),
            (70, 0.9, "Anti-Horny Spray"),
            (90, 0.1, "мокро"),
            (97, 0.1, "Соседи снизу"),
            (97, 0.9, "первой жертвой"),
        ],
    )
    async def test_buckets(self, make_cmd, streamer, monkeypatch, value, random_val, part):
        cmd = make_cmd(HornyGoodCommand)
        monkeypatch.setattr(random, "random", lambda: random_val)
        result = await cmd._handle_new(streamer, "Chatter", "horny", str(value))
        assert part in result

    async def test_change_suffix_low_bucket(self, make_cmd, streamer, monkeypatch):
        cmd = make_cmd(HornyGoodCommand)
        monkeypatch.setattr(random, "random", lambda: 0.9)
        result = await cmd._handle_new(streamer, "Chatter", "horny", "+10")
        assert "побольше стало" in result or "выросло" in result


class TestHornyGoodHandleOld:
    async def test_strips_sign(self, make_cmd, streamer, monkeypatch):
        cmd = make_cmd(HornyGoodCommand)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        result = await cmd._handle_old(streamer, "Chatter", "horny", "+55", "30")
        assert "55%" in result


class TestHornyGoodMisc:
    async def test_cooldown_reply(self, make_cmd):
        result = await make_cmd(HornyGoodCommand)._cooldown_reply("u", 5)
        assert "Потерпи" in result

    async def test_target_selected_mentions_targets(self, make_cmd, monkeypatch):
        monkeypatch.setattr(random, "choice", lambda seq: seq[1])
        result = await make_cmd(HornyGoodCommand)._target_selected("u", ["@a", "@b"])
        assert "@a и @b" in result

    def test_is_enabled(self, make_cmd):
        cmd = make_cmd(HornyGoodCommand)
        assert cmd.is_enabled(SimpleNamespace(enable_horny_good=True)) is True
        assert cmd.is_enabled(SimpleNamespace(enable_horny_good=False)) is False

    async def test_handle_flow(self, make_cmd, sm, send_mock, streamer, monkeypatch):
        cmd = make_cmd(HornyGoodCommand)
        monkeypatch.setattr(random, "randint", lambda a, b: 55)
        await cmd.handle(streamer, make_event("horny"))
        assert "55%" in send_mock.last()
        stored = await sm.get_state(user=77, command="horny_good", param=SMParam.PREVIOUS_VALUE)
        assert stored == "55"

    async def test_handle_cooldown(self, make_cmd, send_mock, streamer, monkeypatch):
        cmd = make_cmd(HornyGoodCommand)
        monkeypatch.setattr(random, "randint", lambda a, b: 55)
        await cmd.handle(streamer, make_event("horny"))
        await cmd.handle(streamer, make_event("horny"))
        assert "Потерпи" in send_mock.last()


# ══════════════════════════════ HORNY BAD ══════════════════════════════════


class TestHornyBadResultGenerator:
    async def test_signs(self, make_cmd, monkeypatch):
        cmd = make_cmd(HornyBadCommand)
        monkeypatch.setattr(random, "randint", lambda a, b: 42)
        assert await cmd.result_generator(None) == "42"
        assert await cmd.result_generator("10") == "+42"
        assert await cmd.result_generator("90") == "-42"
        assert await cmd.result_generator("42") == "42"


class TestHornyBadHandleNew:
    @pytest.mark.parametrize(
        ("value", "random_val", "part"),
        [
            (5, 0.1, "небесного спокойствия"),
            (15, 0.1, "держать себя в руках"),
            (15, 0.9, "самоконтроля"),
            (30, 0.1, "Опасная тропинка"),
            (30, 0.9, "на грани"),
            ("+50", 0.1, "выходит из-под контроля"),
            (50, 0.9, "срочно остыть"),
            (70, 0.1, "крайне тревожно"),
            (70, 0.5, "Anti-Horny Spray"),
            (85, 0.1, "точка невозврата"),
            (85, 0.9, "буря"),
            (97, 0.5, "КРАХ"),
        ],
    )
    async def test_buckets(self, make_cmd, streamer, monkeypatch, value, random_val, part):
        cmd = make_cmd(HornyBadCommand)
        monkeypatch.setattr(random, "random", lambda: random_val)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        result = await cmd._handle_new(streamer, "Chatter", "horny", str(value))
        assert part in result


class TestHornyBadHandleOld:
    async def test_strips_sign(self, make_cmd, streamer, monkeypatch):
        cmd = make_cmd(HornyBadCommand)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        result = await cmd._handle_old(streamer, "Chatter", "horny", "-55", "42")
        assert "55%" in result


class TestHornyBadMisc:
    async def test_target_selected(self, make_cmd):
        result = await make_cmd(HornyBadCommand)._target_selected("u", ["@a"])
        assert "@a" in result

    def test_is_enabled(self, make_cmd):
        cmd = make_cmd(HornyBadCommand)
        assert cmd.is_enabled(SimpleNamespace(enable_horny_bad=True)) is True
        assert cmd.is_enabled(SimpleNamespace(enable_horny_bad=False)) is False

    async def test_handle_flow_and_cooldown(self, make_cmd, send_mock, streamer, monkeypatch):
        cmd = make_cmd(HornyBadCommand)
        monkeypatch.setattr(random, "randint", lambda a, b: 66)
        monkeypatch.setattr(random, "choice", lambda seq: seq[0])
        await cmd.handle(streamer, make_event("horny"))
        assert "66%" in send_mock.last()
        await cmd.handle(streamer, make_event("horny"))
        last = send_mock.last()
        assert "тормози" in last or "суетишься" in last
