import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete

from database.models import User, TwitchUserSettings
from schemas.twitch import ChatMessageWebhookEventSchema
from twitch.chat.bot import ChatBot
from twitch.chat.commands.pants import PantsCommand
from twitch.state_manager import SMParam


class MockStateManager:
    def __init__(self):
        self.state = {}

    async def get_state(self, channel=None, command=None, param=None, user=None, **kwargs):
        key = (channel, command, param, user)
        return self.state.get(key)

    async def set_state(self, value=None, channel=None, command=None, param=None, user=None, **kwargs):
        key = (channel, command, param, user)
        self.state[key] = value

    async def del_state(self, channel=None, command=None, param=None, user=None, **kwargs):
        key = (channel, command, param, user)
        if key in self.state:
            del self.state[key]

    @asynccontextmanager
    async def lifespan(self):
        yield


@pytest_asyncio.fixture
async def pants_bot(container, db_session, mock_twitch):
    from contextlib import asynccontextmanager
    from container_runtime import set_container
    set_container(container)

    sm = MockStateManager()
    bot = ChatBot(
        db_session_factory=MagicMock(return_value=db_session),
        state_manager=sm,
        mqtt=container.mqtt()
    )
    await bot.startup(mock_twitch)
    bot._db_session_factory = MagicMock(return_value=db_session)

    # Actually add users to user_list_manager so extract_targets finds them
    from time import time
    users = ["streamer", "viewer1", "viewer2", "victim", "viewer3", "viewer4", "viewer5",
             "streamer2001", "streamer3001", "streamer4001", "streamer5001",
             "victim", "streamer", "viewer", "victim"]

    # We must patch get_last_active_users and get_active_users to return our test users
    # And we must ensure these mocks are available everywhere they are called

    bot.get_last_active_users = AsyncMock(side_effect=lambda channel: [(u, time()) for u in users])
    bot.get_random_active_user = AsyncMock(return_value="victim")
    bot._user_list_manager.get_active_users = MagicMock(side_effect=lambda channel, timeout=None: [(u, time()) for u in users])

    # The command instance also needs to see these mocks
    for cmd in bot._command_manager.commands:
        if isinstance(cmd, PantsCommand):
            cmd.chat_bot = bot

    return bot, sm


def make_msg(text, chatter_login, streamer_login="streamer", streamer_id=1001):
    # Unique IDs for different chatters to avoid issues if needed
    import random
    chatter_id = random.randint(2000, 9000)
    return ChatMessageWebhookEventSchema.model_validate({
        "broadcaster_user_id": streamer_id,
        "broadcaster_user_login": streamer_login,
        "broadcaster_user_name": streamer_login,
        "chatter_user_id": 2000 + hash(chatter_login) % 1000,
        "chatter_user_login": chatter_login,
        "chatter_user_name": chatter_login,
        "message_id": str(uuid.uuid4()),
        "message": {
            "text": text,
            "fragments": [{"type": "text", "text": text, "cheermote": None, "emote": None, "mention": None}]
        },
        "color": "#FFFFFF",
        "badges": [],
        "message_type": "text"
    })


@pytest.mark.asyncio
async def test_pants_raffle_no_participants(pants_bot, db_session, mock_twitch):
    bot, sm = pants_bot
    streamer_id = 2001
    streamer_login = "streamer2001"

    # Setup DB
    user = User(twitch_id=str(streamer_id), login_name=streamer_login, profile_image_url="http://img", _access_token="a", _refresh_token="r")
    db_session.add(user)
    await db_session.commit()
    await db_session.execute(delete(TwitchUserSettings).where(TwitchUserSettings.user_id == user.id))
    u_settings = TwitchUserSettings(user_id=user.id, enable_pants=True, enable_chat_bot=True)
    db_session.add(u_settings)
    await db_session.commit()

    # 1. Start raffle
    msg = make_msg("!трусы @victim", "viewer1", streamer_login=streamer_login, streamer_id=streamer_id)
    with patch('twitch.chat.commands.pants.asyncio.create_task', side_effect=lambda x: x) as mock_task:
        await bot.on_message(msg)

    mock_twitch.send_chat_message.assert_called_with(
        stream_channel_id=str(streamer_id),
        stream_channel_login=streamer_login,
        message="Внимание, объявляется розыгрыш трусов @victim! Ставьте '+' в чат, чтобы принять участие в розыгрыше!",
        reply_parent_message_id=None
    )

    # 2. Finish raffle with 0 participants
    # Find the PantsCommand instance
    pants_cmd = next(c for c in bot._command_manager.commands if isinstance(c, PantsCommand))
    mock_twitch.send_chat_message.reset_mock()

    await pants_cmd.finish_raffle(user, "victim")

    mock_twitch.send_chat_message.assert_called_once()
    assert "никто не принял участие" in mock_twitch.send_chat_message.call_args[1]["message"]


@pytest.mark.asyncio
async def test_pants_raffle_5_participants_and_winner(pants_bot, db_session, mock_twitch):
    bot, sm = pants_bot
    streamer_id = 3001
    streamer_login = "streamer3001"

    # Setup DB
    user = User(twitch_id=str(streamer_id), login_name=streamer_login, profile_image_url="http://img", _access_token="a", _refresh_token="r")
    db_session.add(user)
    await db_session.commit()
    await db_session.execute(delete(TwitchUserSettings).where(TwitchUserSettings.user_id == user.id))
    u_settings = TwitchUserSettings(user_id=user.id, enable_pants=True, enable_chat_bot=True)
    db_session.add(u_settings)
    await db_session.commit()

    # 1. Start raffle
    await bot.on_message(make_msg("!трусы @victim", "streamer", streamer_login=streamer_login, streamer_id=streamer_id))
    mock_twitch.send_chat_message.reset_mock()

    # 2. Add 5 participants
    for i in range(1, 6):
        await bot.on_message(make_msg("+", f"viewer{i}", streamer_login=streamer_login, streamer_id=streamer_id))

    # Check that at 5th participant a special message was sent
    # 5th '+' message sends: "Уже целых 5 человек хотят заполучить трусы @victim! ..."
    assert mock_twitch.send_chat_message.called
    any_5_msg = any("целых 5 человек" in call[1]["message"] for call in mock_twitch.send_chat_message.call_args_list)
    assert any_5_msg

    # 3. Finish raffle
    pants_cmd = next(c for c in bot._command_manager.commands if isinstance(c, PantsCommand))
    mock_twitch.send_chat_message.reset_mock()

    # To make random.choice predictable or just check outcomes
    with patch('random.choice', return_value="viewer3"):
        with patch('asyncio.sleep', return_value=None):
            await pants_cmd.finish_raffle(user, "victim")

    # Check winner announcement
    any_winner_msg = any("@viewer3! Поздравляем, сегодня ты становишься счастливым обладателем трусов @victim!" in call[1]["message"] for call in mock_twitch.send_chat_message.call_args_list)
    assert any_winner_msg


@pytest.mark.asyncio
async def test_pants_raffle_self_win(pants_bot, db_session, mock_twitch):
    bot, sm = pants_bot
    streamer_id = 4001
    streamer_login = "streamer4001"
    user = User(twitch_id=str(streamer_id), login_name=streamer_login, profile_image_url="http://img", _access_token="a", _refresh_token="r")
    db_session.add(user)
    await db_session.commit()
    await db_session.execute(delete(TwitchUserSettings).where(TwitchUserSettings.user_id == user.id))
    db_session.add(TwitchUserSettings(user_id=user.id, enable_pants=True, enable_chat_bot=True))
    await db_session.commit()

    await bot.on_message(make_msg("!трусы @victim", "streamer", streamer_login=streamer_login, streamer_id=streamer_id))
    await bot.on_message(make_msg("+", "victim", streamer_login=streamer_login, streamer_id=streamer_id)) # Victim participates in their own raffle

    pants_cmd = next(c for c in bot._command_manager.commands if isinstance(c, PantsCommand))
    mock_twitch.send_chat_message.reset_mock()

    with patch('random.choice', return_value="victim"):
        with patch('asyncio.sleep', return_value=None):
            await pants_cmd.finish_raffle(user, "victim")

    any_self_win_msg = any("собственных трусов" in call[1]["message"] for call in mock_twitch.send_chat_message.call_args_list)
    assert any_self_win_msg


@pytest.mark.asyncio
async def test_pants_raffle_cancel_by_minus(pants_bot, db_session, mock_twitch):
    bot, sm = pants_bot
    streamer_id = 5001
    streamer_login = "streamer5001"
    user = User(twitch_id=str(streamer_id), login_name=streamer_login, profile_image_url="http://img", _access_token="a", _refresh_token="r")
    db_session.add(user)
    await db_session.commit()
    await db_session.execute(delete(TwitchUserSettings).where(TwitchUserSettings.user_id == user.id))
    db_session.add(TwitchUserSettings(user_id=user.id, enable_pants=True, enable_chat_bot=True))
    await db_session.commit()

    await bot.on_message(make_msg("!трусы @victim", "streamer", streamer_login=streamer_login, streamer_id=streamer_id))
    mock_twitch.send_chat_message.reset_mock()

    # Victim says "-"
    await bot.on_message(make_msg("-", "victim", streamer_login=streamer_login, streamer_id=streamer_id))

    mock_twitch.send_chat_message.assert_called_once()
    assert "розыгрыш отменяется" in mock_twitch.send_chat_message.call_args[1]["message"]

    # Ensure state is cleared
    assert await sm.get_state(streamer_login, PantsCommand.command_name, SMParam.USER) is None
