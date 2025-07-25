from uuid import UUID

import httpx
from twitchAPI import Twitch as TwitchClient, Chat
from twitchAPI.object import CustomReward, TwitchUser, ChannelFollowersResult
from twitchAPI.types import AuthScope, CustomRewardRedemptionStatus

from config import settings, user_scope
from database.models import User
from utils.singleton import singleton


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
    async def create_reward(user, reward_title: str, reward_cost: int, reward_description: str, is_user_input_required: bool) -> CustomReward:
        twitch_user = await TwitchClient(settings.twitch_client_id, settings.twitch_client_secret)
        await twitch_user.set_user_authentication(user.access_token, user_scope, user.refresh_token)
        reward = await twitch_user.create_custom_reward(user.twitch_id, reward_title, reward_cost, reward_description, is_user_input_required=is_user_input_required)
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
                "https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                    "grant_type": 'client_credentials'
                }
            )
            app_token = response.json()["access_token"]
            response = await client.post(
                "https://api.twitch.tv/helix/eventsub/subscriptions",
                headers={
                    "Authorization": "Bearer " + app_token,
                    "Client-Id": settings.twitch_client_id,
                    "Content-Type": "application/json"
                },
                json={
                    "type": "channel.channel_points_custom_reward_redemption.add",
                    "version": "1",
                    "condition": {
                        "broadcaster_user_id": user.twitch_id,
                        "reward_id": str(reward_id),
                    },
                    "transport": {
                        "method": "webhook",
                        "callback": str(settings.reward_redemption_webhook) + f"/{user.twitch_id}",
                        "secret": settings.twitch_webhook_secret.get_secret_value(),
                    }
                }
            )
            return response.json()

    @staticmethod
    async def get_user_access_refresh_tokens_by_authorization_code(authorization_code: str) -> tuple[str, str]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                    "code": authorization_code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.login_redirect_url,
                }
            )
            tokens = response.json()
            access_token = tokens["access_token"]
            refresh_token = tokens["refresh_token"]
            return access_token, refresh_token

    @staticmethod
    async def get_self(access_token, refresh_token) -> TwitchUser:
        twitch_user = await TwitchClient(settings.twitch_client_id, settings.twitch_client_secret)
        await twitch_user.set_user_authentication(access_token, [], refresh_token)
        return await anext(twitch_user.get_users())

    @staticmethod
    async def get_followers(user: User) -> ChannelFollowersResult:
        twitch_user = await TwitchClient(settings.twitch_client_id, settings.twitch_client_secret)
        await twitch_user.set_user_authentication(user.access_token, [AuthScope.MODERATOR_READ_FOLLOWERS], user.refresh_token)
        return await twitch_user.get_channel_followers(user.twitch_id, user.twitch_id, first=100)
        # TODO: load all via pagination

    @staticmethod
    async def cancel_redemption(user: User, reward_id: UUID, redemption_id: UUID):
        twitch_user = await TwitchClient(settings.twitch_client_id, settings.twitch_client_secret)
        await twitch_user.set_user_authentication(user.access_token, [AuthScope.CHANNEL_MANAGE_REDEMPTIONS], user.refresh_token)
        await twitch_user.update_redemption_status(user.twitch_id, reward_id, redemption_id, CustomRewardRedemptionStatus.CANCELED)

    @staticmethod
    async def fulfill_redemption(user: User, reward_id: UUID, redemption_id: UUID):
        twitch_user = await TwitchClient(settings.twitch_client_id, settings.twitch_client_secret)
        await twitch_user.set_user_authentication(user.access_token, [AuthScope.CHANNEL_MANAGE_REDEMPTIONS], user.refresh_token)
        await twitch_user.update_redemption_status(user.twitch_id, reward_id, redemption_id, CustomRewardRedemptionStatus.FULFILLED)
