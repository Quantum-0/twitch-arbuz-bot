# from unittest.mock import AsyncMock
#
# import pytest
#
# from dependencies import singletons
#
#
# @pytest.fixture
# async def mocked_twitch(async_session_maker, monkeypatch):
#     # создаём фейковый Twitch и подменяем в singletons
#     fake_twitch = AsyncMock()
#     fake_twitch.get_followers.return_value = AsyncMock(total=42)
#     fake_twitch.set_bot_moder.return_value = None
#     singletons["twitch"] = fake_twitch