from uuid import UUID

import httpx
from twitchAPI import Twitch as TwitchClient, Chat
from twitchAPI.object import CustomReward
from twitchAPI.types import AuthScope

from config import settings, user_scope
from utils import singleton


@singleton
class Twitch():
    _twitch: TwitchClient = None

    def __init__(self):
        pass

    async def startup(self):
        twitch = await TwitchClient(settings.twitch_client_id, settings.twitch_client_secret)
        await twitch.set_user_authentication(settings.bot_access_token, [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT],
                                             settings.bot_refresh_token)
        self._twitch = twitch

    async def build_chat_client(self) -> Chat:
        return await Chat(self._twitch)

    @staticmethod
    async def create_reward(user) -> CustomReward:
        twitch_user = await TwitchClient(settings.twitch_client_id, settings.twitch_client_secret)
        await twitch_user.set_user_authentication(user.access_token, user_scope, user.refresh_token)
        reward = await twitch_user.create_custom_reward(user.twitch_id, "test reward by bot", 123, "meow meow?", is_user_input_required=True)
        return reward

    @staticmethod
    async def delete_reward(user, reward_id: UUID | str):
        twitch_user = await TwitchClient(settings.twitch_client_id, settings.twitch_client_secret)
        await twitch_user.set_user_authentication(user.access_token, user_scope, user.refresh_token)
        await twitch_user.delete_custom_reward(user.twitch_id, str(reward_id))

    @staticmethod
    async def subscribe_reward(user, reward_id: UUID | str):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.twitch.tv/helix/eventsub/subscriptions",
                headers={
                    "Authorization": "Bearer " + user.access_toke,
                    "Client-Id": settings.twitch_client_id,
                    "Content-Type": "application/json"
                },
                data={
                    "type": "channel.channel_points_custom_reward_redemption.add",
                    "version": "1",
                    "condition": {
                        "broadcaster_user_id": user.twitch_id,
                        "reward_id": str(reward_id),
                    },
                    "transport": {
                        "method": "webhook",
                        "callback": settings.reward_redemption_webhook,
                        "secret": settings.twitch_webhook_secret,
                    }
                }
            )
            return response