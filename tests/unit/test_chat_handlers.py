import random
from types import SimpleNamespace

import pytest

from routers.schemas import ChatMessageSchema
from twitch.chat.handlers.handlers import HelloHandler


def _build_webhook_event(user_message: str, display_name: str, login: str):
    return SimpleNamespace(
        message=SimpleNamespace(text=user_message),
        reply=None,
        chatter_user_name=display_name,
        chatter_user_login=login,
    )


@pytest.mark.parametrize(
    "user_message",
    [
        "@Quantum075Bot, привет!",
        "@Quantum075Bot привет Kappa",
        "Дарова @Quantum075Bot",
        "Здравствуй, @Quantum075Bot",
    ],
)
@pytest.mark.parametrize("streamer_name", ["Quantum075", "SomeStreamer"])
@pytest.mark.parametrize(
    "user_name",
    [
        {"display_name": "Vasya", "name": "vasya"},
        {"display_name": "SomeStreamer", "name": "somestreamer"},
        {"display_name": "quantum075", "name": "Quantum075"},
    ],
)
@pytest.mark.asyncio
async def test_hello_positive(
    state_manager, send_message_mock, streamer_name, user_name, user_message
):
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    msg = _build_webhook_event(
        user_message=user_message,
        display_name=user_name["display_name"],
        login=user_name["name"],
    )
    streamer = SimpleNamespace(login_name=streamer_name)

    await handler.handle(streamer, msg)
    send_message_mock.assert_sent(
        f"@{user_name['display_name']}, дарова! >w<", chat=streamer
    )


@pytest.mark.parametrize(
    "user_message", ["@Quantum075Bot, привет!", "@Quantum075Bot кваствуй"]
)
@pytest.mark.parametrize(
    ("streamer_name", "response"),
    [
        ("toad_anna", "@Vasya, кваствуй! >w<"),
        ("anna_toad", "@Vasya, кваствуй! >w<"),
        ("glumarkoj", "@Vasya, здорова, брат!"),
    ],
)
@pytest.mark.asyncio
async def test_hello_custom_reply_in_channels(
    state_manager, send_message_mock, streamer_name, response, user_message
):
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    msg = _build_webhook_event(
        user_message=user_message,
        display_name="Vasya",
        login="vasya",
    )
    streamer = SimpleNamespace(login_name=streamer_name)

    await handler.handle(streamer, msg)
    send_message_mock.assert_sent(response, chat=streamer)


@pytest.mark.parametrize(
    "user_message",
    ["@Quantum075Bot, пырывееет!", "@Quantum075Bot йоу", "@Quantum075Bob привет"],
)
@pytest.mark.asyncio
async def test_hello_negative(state_manager, send_message_mock, user_message):
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    msg = _build_webhook_event(
        user_message=user_message,
        display_name="Vasya",
        login="vasya",
    )
    streamer = SimpleNamespace(login_name="TestStreamer")

    await handler.handle(streamer, msg)
    send_message_mock.assert_not_sent()


async def test_new_message(
    state_manager,
    send_message_mock,
    twitch_message_event_model: ChatMessageSchema,
):
    msg = twitch_message_event_model.event
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    streamer = SimpleNamespace(login_name="TestStreamer")

    await handler.handle(streamer, msg)
    send_message_mock.assert_not_sent()
