# ruff: noqa: S101, ARG001

import random
from time import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from schemas.twitch import ChatMessageSchema, ChatMessageWebhookEventSchema
from twitch.chat.handlers.handlers import (
    HelloHandler,
    IAmBotHandler,
    MessagesHandlerManager,
    PyramidHandler,
    UnlurkHandler,
)
from twitch.chat.handlers.memes import GiveMemesHandler
from twitch.chat.handlers.pants_raffle import PantsRaffleHandler
from twitch.chat.handlers.thanks import ThanksHandler


def _streamer(login: str) -> SimpleNamespace:
    return SimpleNamespace(login_name=login)


def _msg(
    login: str, display_name: str, text: str, reply_parent_name: str | None = None
) -> ChatMessageWebhookEventSchema:
    """Сообщение channel.chat.message в формате EventSub (как его ждут хендлеры)."""
    event = {
        "broadcaster_user_id": 1,
        "broadcaster_user_login": "streamer",
        "broadcaster_user_name": "Streamer",
        "chatter_user_id": 2,
        "chatter_user_login": login,
        "chatter_user_name": display_name,
        "message_id": str(uuid4()),
        "message": {"text": text, "fragments": []},
        "color": "#FFFFFF",
        "badges": [],
        "message_type": "text",
    }
    if reply_parent_name is not None:
        event["reply"] = {
            "parent_message_id": "pm-1",
            "parent_message_body": "parent body",
            "parent_user_id": "3",
            "parent_user_name": reply_parent_name,
            "parent_user_login": "quantum075bot",
            "thread_message_id": "tm-1",
            "thread_user_id": "3",
            "thread_user_name": reply_parent_name,
            "thread_user_login": "quantum075bot",
        }
    return ChatMessageWebhookEventSchema.model_validate(event)


def _sent_messages(send_message_mock) -> set[str]:
    return {text for _, text in send_message_mock._calls}


@pytest.mark.parametrize(
    "user_message",
    [
        "@Quantum075Bot, привет!",
        "@Quantum075Bot привет Kappa",
        "Дарова @Quantum075Bot",
        "Здравствуй, @Quantum075Bot",
    ],
)
@pytest.mark.parametrize("streamer_login", ["quantum075", "somestreamer"])
@pytest.mark.parametrize(
    ("login", "display_name"),
    [
        ("vasya", "Vasya"),
        ("somestreamer", "SomeStreamer"),
        ("Quantum075", "quantum075"),
    ],
)
async def test_hello_positive(state_manager, send_message_mock, streamer_login, login, display_name, user_message):
    random.seed(42)
    streamer = _streamer(streamer_login)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)

    result = await handler.handle(streamer, _msg(login, display_name, user_message))

    assert result.name == "HANDLED"
    expected_replies = {
        f"@{display_name}, и тебе привет!",
        f"@{display_name}, здравствуй-здравствуй!",
        f"@{display_name}, дарова! >w<",
    }
    assert _sent_messages(send_message_mock) & expected_replies
    assert all(chat is streamer for chat, _ in send_message_mock._calls)


@pytest.mark.parametrize("user_message", ["@Quantum075Bot, привет!", "@Quantum075Bot кваствуй"])
@pytest.mark.parametrize(
    ("streamer_login", "expected_replies"),
    [
        ("toad_anna", {"@Vasya, кваствуй! >w<", "Кваствуй, @Vasya! <3"}),
        ("anna_toad", {"@Vasya, кваствуй! >w<", "Кваствуй, @Vasya! <3"}),
        ("glumarkoj", {"@Vasya, здорова, брат!"}),
    ],
)
async def test_hello_custom_reply_in_channels(
    state_manager, send_message_mock, streamer_login, expected_replies, user_message
):
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)

    await handler.handle(_streamer(streamer_login), _msg("vasya", "Vasya", user_message))

    assert _sent_messages(send_message_mock) & expected_replies


@pytest.mark.parametrize(
    "user_message",
    ["@Quantum075Bot, пырывееет!", "@Quantum075Bot йоу", "@Quantum075Bob привет"],
)
async def test_hello_negative(state_manager, send_message_mock, user_message):
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)

    result = await handler.handle(_streamer("teststreamer"), _msg("vasya", "Vasya", user_message))

    assert result.name == "SKIPED"
    send_message_mock.assert_not_sent()


async def test_hello_reply_to_bot_adds_mention(state_manager, send_message_mock):
    """Ответ на сообщение бота эквивалентен обращению: приветствие без упоминания тоже триггерит ответ."""
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)

    result = await handler.handle(
        _streamer("teststreamer"),
        _msg("vasya", "Vasya", "привет!", reply_parent_name="quantum075bot"),
    )

    assert result.name == "HANDLED"
    assert _sent_messages(send_message_mock)


async def test_hello_reply_to_other_user_ignored(state_manager, send_message_mock):
    """Ответ чужому человеку без упоминания бота — не приветствие боту."""
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)

    result = await handler.handle(
        _streamer("teststreamer"),
        _msg("vasya", "Vasya", "привет!", reply_parent_name="SomebodyElse"),
    )

    assert result.name == "SKIPED"
    send_message_mock.assert_not_sent()


async def test_new_message(
    state_manager,
    send_message_mock,
    twitch_message_event_model: ChatMessageSchema,
    test_user,
):
    msg = twitch_message_event_model.event
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    await handler.handle("TestStreamer", msg)
    send_message_mock.assert_not_sent()


# ───────────────────────────── GiveMemesHandler ─────────────────────────────


def _streamer_ma(reward: UUID | None) -> SimpleNamespace:
    return SimpleNamespace(login_name="teststreamer", memealerts=SimpleNamespace(memealerts_reward=reward))


_MEMES_BASE_REPLIES = {"@Vasya, неть", "@Vasya, не дам", "@Vasya, не хЪ"}
_MEMES_REWARD_REPLIES = {
    "@Vasya, только через награду 👀",
    "@Vasya, только если хорошо попросишь",
    "@Vasya, воспользуйся наградой!",
}


@pytest.mark.parametrize(
    "text", ["@Quantum075Bot дай мемов", "дай мемаф @Quantum075Bot", "@Quantum075Bot, ну дай мем!"]
)
async def test_give_memes_positive(state_manager, send_message_mock, text):
    """Адресованное боту «дай мем…» — отказ из базового пула."""
    handler = GiveMemesHandler(sm=state_manager, send_message=send_message_mock)

    result = await handler.handle(_streamer_ma(None), _msg("vasya", "Vasya", text))

    assert result.name == "HANDLED"
    sent = _sent_messages(send_message_mock)
    assert sent & _MEMES_BASE_REPLIES
    assert sent <= _MEMES_BASE_REPLIES


async def test_give_memes_reply_to_bot(state_manager, send_message_mock):
    handler = GiveMemesHandler(sm=state_manager, send_message=send_message_mock)

    result = await handler.handle(
        _streamer_ma(None), _msg("vasya", "Vasya", "дай мемафов", reply_parent_name="quantum075bot")
    )

    assert result.name == "HANDLED"
    assert _sent_messages(send_message_mock) & _MEMES_BASE_REPLIES


@pytest.mark.parametrize(
    ("text", "reply_parent"),
    [
        ("дай мемов", None),  # без адресации боту
        ("дай мемов", "SomebodyElse"),  # реплай чужому человеку
        ("@Quantum075Bot дай конфет", None),  # не про мемы
        ("@Quantum075Bot мемов!", None),  # нет «дай»
    ],
)
async def test_give_memes_negative(state_manager, send_message_mock, text, reply_parent):
    handler = GiveMemesHandler(sm=state_manager, send_message=send_message_mock)

    result = await handler.handle(_streamer_ma(None), _msg("vasya", "Vasya", text, reply_parent_name=reply_parent))

    assert result.name == "SKIPED"
    send_message_mock.assert_not_sent()


async def test_give_memes_reward_pool(state_manager, send_message_mock):
    """Награда мемкоинов подключена — к пулу добавляются наводящие варианты."""
    handler = GiveMemesHandler(sm=state_manager, send_message=send_message_mock)
    sent = set()
    for seed in range(50):
        random.seed(seed)
        await handler.handle(
            _streamer_ma(UUID("11111111-1111-1111-1111-111111111111")),
            _msg("vasya", "Vasya", "@Quantum075Bot дай мемов"),
        )
    sent = _sent_messages(send_message_mock)

    assert sent <= _MEMES_BASE_REPLIES | _MEMES_REWARD_REPLIES
    assert sent & _MEMES_BASE_REPLIES
    assert sent & _MEMES_REWARD_REPLIES


async def test_give_memes_no_reward_pool(state_manager, send_message_mock):
    """Награда не подключена — только базовые отказы."""
    handler = GiveMemesHandler(sm=state_manager, send_message=send_message_mock)
    for seed in range(50):
        random.seed(seed)
        await handler.handle(_streamer_ma(None), _msg("vasya", "Vasya", "@Quantum075Bot дай мемов"))

    assert _sent_messages(send_message_mock) <= _MEMES_BASE_REPLIES
    assert _sent_messages(send_message_mock) & _MEMES_BASE_REPLIES


@pytest.mark.parametrize(
    ("text", "reply_parent", "expected"),
    [
        ("@Quantum075Bot дай мемов", None, True),
        ("дай мемов", "quantum075bot", True),
        ("дай мемов", None, False),
        ("@Quantum075Bot дай конфет", None, False),
    ],
)
async def test_give_memes_is_candidate(state_manager, send_message_mock, text, reply_parent, expected):
    handler = GiveMemesHandler(sm=state_manager, send_message=send_message_mock)
    assert await handler.is_candidate(_msg("vasya", "Vasya", text, reply_parent_name=reply_parent)) is expected


# ───────────────────────── is_candidate-предикаты хендлеров ─────────────────────────


@pytest.mark.parametrize(
    ("text", "reply_parent", "expected"),
    [
        ("@Quantum075Bot спасибо!", None, True),
        ("пасибки @Quantum075Bot", None, True),
        ("спасибо", "quantum075bot", True),
        ("спасибо всем", None, False),  # без адресации боту
        ("@Quantum075Bot привет", None, False),  # не благодарность
        ("@Quantum075Bot", None, False),  # пусто после очистки тега
    ],
)
async def test_thanks_is_candidate(state_manager, send_message_mock, text, reply_parent, expected):
    handler = ThanksHandler(sm=state_manager, send_message=send_message_mock)
    assert await handler.is_candidate(_msg("vasya", "Vasya", text, reply_parent_name=reply_parent)) is expected


@pytest.mark.parametrize(
    ("text", "reply_parent", "expected"),
    [
        ("привет @Quantum075Bot", None, True),
        ("всем привет", None, True),
        ("привет!", "quantum075bot", True),
        ("привет!", None, False),
        ("привет!", "SomebodyElse", False),
        ("йоу @Quantum075Bot", None, False),
    ],
)
async def test_hello_is_candidate(state_manager, send_message_mock, text, reply_parent, expected):
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    assert await handler.is_candidate(_msg("vasya", "Vasya", text, reply_parent_name=reply_parent)) is expected


@pytest.mark.parametrize(
    ("text", "reply_parent", "expected"),
    [
        ("@quantum075bot бот?", None, True),
        ("@quantum075bot ты бот?", None, True),
        ("ты бот?", "quantum075bot", True),  # HelloHandler допишет тег к реплаю
        ("боты плюс в чат", None, True),
        ("ты бот?", None, False),
        ("ты бот?", "SomebodyElse", False),
        ("@quantum075bot, ты не бот", None, False),
        ("боты плюс", None, False),  # « в чат» — обязательная часть триггера
    ],
)
async def test_iam_bot_is_candidate(state_manager, send_message_mock, text, reply_parent, expected):
    handler = IAmBotHandler(sm=state_manager, send_message=send_message_mock)
    assert await handler.is_candidate(_msg("vasya", "Vasya", text, reply_parent_name=reply_parent)) is expected


@pytest.mark.parametrize(
    ("text", "expected"), [("+", True), ("-", True), (" + ", True), ("++", False), ("плюс", False)]
)
async def test_pants_raffle_is_candidate(state_manager, send_message_mock, text, expected):
    handler = PantsRaffleHandler(sm=state_manager, send_message=send_message_mock)
    assert await handler.is_candidate(_msg("vasya", "Vasya", text)) is expected


async def test_unlurk_is_candidate(state_manager, send_message_mock, monkeypatch):
    """Кандидат — только сообщение от юзера с лурк-стейтом (и не командой лурка)."""
    fake_container = SimpleNamespace(chat_bot=lambda: object())
    monkeypatch.setattr("container_runtime.get_container", lambda: fake_container)
    handler = UnlurkHandler(sm=state_manager, send_message=send_message_mock)

    assert not await handler.is_candidate(_msg("vasya", "Vasya", "привет"))

    await state_manager.set_state(channel="streamer", user="vasya", command="lurk", value=time())
    assert await handler.is_candidate(_msg("vasya", "Vasya", "привет"))
    assert not await handler.is_candidate(_msg("vasya", "Vasya", "!лурк"))


async def test_pyramid_is_candidate_always_false(state_manager, send_message_mock):
    """PyramidHandler выключен хардкодом — кандидатом быть не может."""
    handler = PyramidHandler(sm=state_manager, send_message=send_message_mock)
    assert not await handler.is_candidate(_msg("vasya", "Vasya", "Kappa"))


class _FakeSSEManager:
    def __init__(self, has_clients: bool) -> None:
        self._has = has_clients

    async def has_clients(self, user_id: int, channel) -> bool:  # noqa: ARG002
        return self._has


async def test_tts_is_candidate(state_manager, send_message_mock, monkeypatch):
    """Кандидат — только при подключённом TTS-оверлее; редемпшны наград не озвучиваются."""
    from twitch.chat.handlers.tts import TTSHandler

    sse = _FakeSSEManager(has_clients=False)
    fake_container = SimpleNamespace(sse_manager=lambda: sse)
    monkeypatch.setattr("container_runtime.get_container", lambda: fake_container)
    handler = TTSHandler(sm=state_manager, send_message=send_message_mock)

    assert not await handler.is_candidate(_msg("vasya", "Vasya", "привет"))

    sse._has = True
    assert await handler.is_candidate(_msg("vasya", "Vasya", "привет"))

    reward_msg = _msg("vasya", "Vasya", "привет")
    object.__setattr__(reward_msg, "channel_points_custom_reward_id", "reward-1")
    assert not await handler.is_candidate(reward_msg)


async def test_handler_manager_is_any_candidate(state_manager, send_message_mock, monkeypatch):
    """Менеджер: кандидат, если хотя бы один хендлер кандидат (Thanks — да, Pyramid — нет)."""
    fake_container = SimpleNamespace(chat_bot=lambda: object())
    monkeypatch.setattr("container_runtime.get_container", lambda: fake_container)
    manager = MessagesHandlerManager(state_manager, send_message_mock)
    manager.register(ThanksHandler)
    manager.register(PyramidHandler)

    assert await manager.is_any_candidate(_msg("vasya", "Vasya", "@Quantum075Bot спасибо"))
    assert not await manager.is_any_candidate(_msg("vasya", "Vasya", "просто болтовня"))
