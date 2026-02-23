import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import httpx
from more_itertools.recipes import batched
from twitchAPI.chat import Chat
from twitchAPI.object.api import (
    ChannelFollowersResult,
    CustomReward,
    Moderator,
    SendMessageResponse,
    Stream,
    TwitchUser,
    EventSubSubscription,
    UserActiveExtensions,
)
from twitchAPI.twitch import Twitch as TwitchClient
from twitchAPI.type import AuthScope, CustomRewardRedemptionStatus, UnauthorizedException

from config import bot_scope, settings, user_scope
from database.models import User
from utils.singleton import singleton

logger = logging.getLogger(__name__)


@singleton
class Twitch:
    _twitch: TwitchClient = None  # type: ignore

    def __init__(self):
        pass

    async def startup(self):
        twitch = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch.set_user_authentication(
            settings.bot_access_token, bot_scope, settings.bot_refresh_token
        )
        self._twitch = twitch
        # await self.get_subscriptions()

    async def build_chat_client(self) -> Chat:
        return await Chat(self._twitch)

    async def shoutout(self, user: User, shoutout_to: int) -> None:
        await self._twitch.send_a_shoutout(
            from_broadcaster_id=user.twitch_id,
            to_broadcaster_id=str(shoutout_to),
            moderator_id="957818216",
        )

    @staticmethod
    async def create_reward(
        user,
        reward_title: str,
        reward_cost: int,
        reward_description: str,
        is_user_input_required: bool,
    ) -> CustomReward:
        twitch_user = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch_user.set_user_authentication(
            user.access_token, user_scope, user.refresh_token
        )
        reward = await twitch_user.create_custom_reward(
            user.twitch_id,
            reward_title,
            reward_cost,
            reward_description,
            is_user_input_required=is_user_input_required,
        )
        return reward

    async def get_streams(self, users: list[User] | list[str]) -> dict[User, Stream | None]:
        streams = {}
        for batch in batched(users, 100):
            streams.update({
                x.user_login: x
                async for x in self._twitch.get_streams(
                    user_login=[(user if isinstance(user, str) else user.login_name) for user in batch]
                )
            })
        return {user: streams.get(user if isinstance(user, str) else user.login_name) for user in users}

    @staticmethod
    async def delete_reward(user, reward_id: UUID | str):
        twitch_user = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch_user.set_user_authentication(
            user.access_token, user_scope, user.refresh_token
        )
        await twitch_user.delete_custom_reward(user.twitch_id, str(reward_id))

    async def send_chat_message(
        self,
        stream_channel: User,
        message: str,
        reply_parent_message_id: str | None = None,
        for_source_only: bool | None = None,
    ) -> SendMessageResponse:
        result = await self._twitch.send_chat_message(
            broadcaster_id=stream_channel.twitch_id,
            sender_id="957818216",
            message=message,
            reply_parent_message_id=reply_parent_message_id,
            for_source_only=for_source_only,
        )
        return result

    async def get_subscriptions(self) -> list[EventSubSubscription]:
        try:
            result = await self._twitch.get_eventsub_subscriptions()
            res = []
            async for item in result:
                res.append(item)
            return res
        except UnauthorizedException:
            # await self._twitch.refresh_used_token() ???
            twitch = await TwitchClient(
                settings.twitch_client_id, settings.twitch_client_secret
            )
            await twitch.set_user_authentication(
                settings.bot_access_token, bot_scope, settings.bot_refresh_token
            )
            self._twitch = twitch
            result = await self._twitch.get_eventsub_subscriptions()
        res = []
        async for item in result:
            res.append(item)
        return res

    async def subscribe_chat_messages(
        self, *users: tuple[User, ...]
    ) -> AsyncGenerator[tuple[User, bool, dict[str, Any]]]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                    "grant_type": "client_credentials",
                },
            )
            app_token = response.json()["access_token"]
            for user in users:
                logger.info(
                    f"Subscribing to messages to channel `{user.login_name}`..."
                )
                response = await client.post(
                    "https://api.twitch.tv/helix/eventsub/subscriptions",
                    headers={
                        "Authorization": "Bearer " + app_token,
                        "Client-Id": settings.twitch_client_id,
                        "Content-Type": "application/json",
                    },
                    json={
                        "type": "channel.chat.message",
                        "version": "1",
                        "condition": {
                            "broadcaster_user_id": str(user.twitch_id),
                            "user_id": "957818216",
                        },
                        "transport": {
                            "method": "webhook",
                            "callback": str(settings.reward_redemption_webhook)
                            + f"/{user.twitch_id}",
                            "secret": settings.twitch_webhook_secret.get_secret_value(),
                        },
                    },
                )
                if not response.is_success:
                    logger.error(response.json())
                yield user, response.is_success, response.json()

    async def unsubscribe_event_sub(self, sub_id: UUID | str):
        await self._twitch.delete_eventsub_subscription(str(sub_id))

    @staticmethod
    async def subscribe_reward(user, reward_id: UUID | str):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                    "grant_type": "client_credentials",
                },
            )
            app_token = response.json()["access_token"]
            response = await client.post(
                "https://api.twitch.tv/helix/eventsub/subscriptions",
                headers={
                    "Authorization": "Bearer " + app_token,
                    "Client-Id": settings.twitch_client_id,
                    "Content-Type": "application/json",
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
                        "callback": str(settings.reward_redemption_webhook)
                        + f"/{user.twitch_id}",
                        "secret": settings.twitch_webhook_secret.get_secret_value(),
                    },
                },
            )
            response.raise_for_status()
            return response.json()

    async def subscribe_raid(self, user: User):
        # result = await self._twitch.create_eventsub_subscription(
        #     "channel.raid",
        #     version="1",
        #     condition={
        #         "to_broadcaster_user_id": user.twitch_id,
        #     },
        #     transport={
        #         "method": "webhook",
        #         "callback": str(settings.reward_redemption_webhook) + f"/{user.twitch_id}",
        #         "secret": settings.twitch_webhook_secret.get_secret_value(),
        #     },
        # )
        # return result
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                    "grant_type": "client_credentials",
                },
            )
            app_token = response.json()["access_token"]
            response = await client.post(
                "https://api.twitch.tv/helix/eventsub/subscriptions",
                headers={
                    "Authorization": "Bearer " + app_token,
                    "Client-Id": settings.twitch_client_id,
                    "Content-Type": "application/json",
                },
                json={
                    "type": "channel.raid",
                    "version": "1",
                    "condition": {
                        "to_broadcaster_user_id": user.twitch_id,
                    },
                    "transport": {
                        "method": "webhook",
                        "callback": str(settings.reward_redemption_webhook)
                        + f"/{user.twitch_id}",
                        "secret": settings.twitch_webhook_secret.get_secret_value(),
                    },
                },
            )
            response.raise_for_status()
            return response.json()

    async def unsubscribe_raid(
        self, *, user: User = None, subscription_id: UUID = None
    ):
        assert bool(user) != bool(subscription_id)
        if user:
            subscriptions = await self.get_subscriptions()
            for sub in subscriptions:
                if sub.type == "channel.raid" and sub.condition[
                    "to_broadcaster_user_id"
                ] == str(user.twitch_id):
                    await self._twitch.delete_eventsub_subscription(
                        subscription_id=sub.id
                    )
                    return True
            return False
        elif subscription_id:
            await self._twitch.delete_eventsub_subscription(
                subscription_id=str(subscription_id)
            )
            return True
        # async with httpx.AsyncClient() as client:
        #     response = await client.post(
        #         "https://id.twitch.tv/oauth2/token",
        #         params={
        #             "client_id": settings.twitch_client_id,
        #             "client_secret": settings.twitch_client_secret,
        #             "grant_type": 'client_credentials'
        #         }
        #     )
        #     app_token = response.json()["access_token"]
        #     response = await client.delete(
        #         "https://api.twitch.tv/helix/eventsub/subscriptions",
        #         headers={
        #             "Authorization": "Bearer " + app_token,
        #             "Client-Id": settings.twitch_client_id,
        #             "Content-Type": "application/json"
        #         },
        #         params={
        #             "id": str(subscription_id),
        #         }
        #     )
        #     response.raise_for_status()
        #     return response.json()

    @staticmethod
    async def get_user_access_refresh_tokens_by_authorization_code(
        authorization_code: str,
    ) -> tuple[str, str]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                    "code": authorization_code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.login_redirect_url,
                },
            )
            tokens = response.json()
            try:
                access_token = tokens["access_token"]
                refresh_token = tokens["refresh_token"]
            except KeyError:
                logger.error(f"Error getting tokens from oauth. Resp: {tokens}")
            return access_token, refresh_token

    @staticmethod
    async def get_self(access_token, refresh_token) -> TwitchUser:
        twitch_user = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch_user.set_user_authentication(access_token, [], refresh_token)
        return await anext(twitch_user.get_users())

    @staticmethod
    async def set_bot_moder(user: User) -> None:
        twitch_user = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch_user.set_user_authentication(
            user.access_token,
            [AuthScope.CHANNEL_MANAGE_MODERATORS, AuthScope.MODERATION_READ],
            user.refresh_token,
        )
        mods: AsyncGenerator[Moderator] = twitch_user.get_moderators(
            user.twitch_id, first=100
        )
        async for mod in mods:
            if mod.user_id == "957818216":
                return
        # return await twitch_user.get_channel_followers(user.twitch_id, user.twitch_id, first=100)
        await twitch_user.add_channel_moderator(user.twitch_id, "957818216")

    @staticmethod
    async def get_followers(user: User) -> ChannelFollowersResult:
        twitch_user = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch_user.set_user_authentication(
            user.access_token, [AuthScope.MODERATOR_READ_FOLLOWERS], user.refresh_token
        )
        return await twitch_user.get_channel_followers(
            user.twitch_id, user.twitch_id, first=100
        )
        # TODO: load all via pagination

    @staticmethod
    async def cancel_redemption(user: User, reward_id: UUID, redemption_id: UUID):
        twitch_user = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch_user.set_user_authentication(
            user.access_token,
            [AuthScope.CHANNEL_MANAGE_REDEMPTIONS],
            user.refresh_token,
        )
        await twitch_user.update_redemption_status(
            user.twitch_id,
            reward_id,
            redemption_id,
            CustomRewardRedemptionStatus.CANCELED,
        )

    @staticmethod
    async def fulfill_redemption(user: User, reward_id: UUID, redemption_id: UUID):
        twitch_user = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch_user.set_user_authentication(
            user.access_token,
            [AuthScope.CHANNEL_MANAGE_REDEMPTIONS],
            user.refresh_token,
        )
        await twitch_user.update_redemption_status(
            user.twitch_id,
            reward_id,
            redemption_id,
            CustomRewardRedemptionStatus.FULFILLED,
        )

    async def get_user_active_ext(
        self, user: User,
    ) -> UserActiveExtensions:
        twitch_user = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch_user.set_user_authentication(
            user.access_token,
            [AuthScope.USER_EDIT_BROADCAST, AuthScope.USER_READ_BROADCAST],
            user.refresh_token,
        )
        return await twitch_user.get_user_active_extensions(user.twitch_id)

    async def install_heat_ext(
        self, user: User,
    ):
        twitch_user = await TwitchClient(
            settings.twitch_client_id, settings.twitch_client_secret
        )
        await twitch_user.set_user_authentication(
            user.access_token,
            [AuthScope.USER_EDIT_BROADCAST, AuthScope.USER_READ_BROADCAST],
            user.refresh_token,
        )
        await twitch_user.update_user_extensions(
            UserActiveExtensions(
                overlay={
                    "1": dict(
                        active=True,
                        id="cr20njfkgll4okyrhag7xxph270sqk",
                        version="2.1.1"
                    )
                }
            )
        )