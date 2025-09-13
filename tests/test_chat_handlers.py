import datetime
import random

import pytest
from twitchAPI.chat import ChatMessage

from routers.schemas import ChatMessageSchema
from twitch.handlers import HelloHandler


@pytest.mark.parametrize('user_message', ["@Quantum075Bot, привет!", "@Quantum075Bot привет Kappa", "Дарова @Quantum075Bot", "Здравствуй, @Quantum075Bot"])
@pytest.mark.parametrize('streamer_name', ['Quantum075', 'SomeStreamer'])
@pytest.mark.parametrize('user_name', [{'display_name': 'Vasya', 'name': 'vasya'}, {'display_name': 'SomeStreamer', 'name': 'somestreamer'}, {'display_name': 'quantum075', 'name': 'Quantum075'}])
@pytest.mark.asyncio
async def test_hello_positive(state_manager, send_message_mock, streamer_name, user_name, user_message):
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    msg = ChatMessage(chat={}, parsed={'source': {'nick': user_name['name']}, 'parameters': user_message, 'tags': {'display-name': user_name['display_name'], 'tmi-sent-ts': datetime.datetime.now().timestamp()}})
    await handler.handle(streamer_name, msg)
    send_message_mock.assert_sent(f'@{user_name["display_name"]}, дарова! >w<', chat=streamer_name)


@pytest.mark.parametrize('user_message', ["@Quantum075Bot, привет!", "@Quantum075Bot кваствуй"])
@pytest.mark.parametrize(('streamer_name', 'response'), [('toad_anna', '@Vasya, кваствуй! >w<'), ('anna_toad', '@Vasya, кваствуй! >w<'), ('glumarkoj', '@Vasya, здорова, брат!')])
@pytest.mark.asyncio
async def test_hello_custom_reply_in_channels(state_manager, send_message_mock, streamer_name, response, user_message):
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    msg = ChatMessage(chat={}, parsed={'source': {'nick': 'vasya'}, 'parameters': user_message, 'tags': {'display-name': 'Vasya', 'tmi-sent-ts': datetime.datetime.now().timestamp()}})
    await handler.handle(streamer_name, msg)
    send_message_mock.assert_sent(response, chat=streamer_name)


@pytest.mark.parametrize('user_message', ["@Quantum075Bot, пырывееет!", "@Quantum075Bot йоу", "@Quantum075Bob привет"])
@pytest.mark.asyncio
async def test_hello_negative(state_manager, send_message_mock, user_message):
    random.seed(42)
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    msg = ChatMessage(chat={}, parsed={'source': {'nick': 'vasya'}, 'parameters': user_message, 'tags': {'display-name': 'Vasya', 'tmi-sent-ts': datetime.datetime.now().timestamp()}})
    await handler.handle('TestStreamer', msg)
    send_message_mock.assert_not_sent()

async def test_new_message(state_manager, send_message_mock, twitch_message_event_model: ChatMessageSchema, test_user):
    msg = twitch_message_event_model.event
    handler = HelloHandler(sm=state_manager, send_message=send_message_mock)
    await handler.handle('TestStreamer', msg)
    send_message_mock.assert_not_sent()