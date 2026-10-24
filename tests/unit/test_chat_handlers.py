# ruff: noqa: S101, ARG001

import random
from types import SimpleNamespace
from uuid import uuid4

import pytest

from schemas.twitch import ChatMessageSchema, ChatMessageWebhookEventSchema
from twitch.chat.handlers.handlers import HelloHandler


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
