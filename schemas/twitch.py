from datetime import datetime
from typing import Literal, TypeVar, Generic
from uuid import UUID

from pydantic import BaseModel, Field, AnyHttpUrl

from config import settings


class PointRewardRedemptionWebhookEventSchema(BaseModel):
    redemption_id: UUID = Field(..., alias="id")
    status: str = Field(..., examples=["unfulfilled"])
    user_id: int = Field(..., description="ID зрителя")
    user_name: str = Field(..., examples=["Quantum075"])
    user_input: str = Field(...)
    user_login: str = Field(..., examples=["quantum075"])
    redeemed_at: datetime = Field(...)
    broadcaster_user_id: int = Field(...)
    broadcaster_user_name: str = Field(..., examples=["Quantum075"])
    broadcaster_user_login: str = Field(..., examples=["quantum075"])


class WebhookSubscriptionRewardRedemptionConditionSchema(BaseModel):
    reward_id: UUID = Field(...)
    broadcaster_user_id: int = Field(...)


class WebhookSubscriptionRaidConditionSchema(BaseModel):
    from_broadcaster_user_id: int | Literal[""] = Field(...)
    to_broadcaster_user_id: int | Literal[""] = Field(...)


class WebhookTransportSchema(BaseModel):
    method: str = Field(..., examples=["webhook"])
    callback: AnyHttpUrl = Field(..., examples=[str(settings.reward_redemption_webhook) + "/123"])


T = TypeVar("T", bound=BaseModel)


class WebhookSubscriptionSchema(BaseModel, Generic[T]):
    subscription_id: UUID = Field(..., alias="id")
    cost: int = Field(...)
    type: str = Field(
        ...,
        examples=[
            "channel.channel_points_custom_reward_redemption.add",
            "channel.raid",
        ],
    )
    status: str = Field(..., examples=["enabled"])
    version: int = Field(..., examples=[1])
    condition: T
    transport: WebhookTransportSchema
    created_at: datetime = Field(...)


class PointRewardRedemptionWebhookSchema(BaseModel):
    event: PointRewardRedemptionWebhookEventSchema
    subscription: WebhookSubscriptionSchema[WebhookSubscriptionRewardRedemptionConditionSchema]


class TwitchChallengeSchema(BaseModel):
    challenge: str


class RaidWebhookEventSchema(BaseModel):
    from_broadcaster_user_id: int = Field(...)
    from_broadcaster_user_name: str = Field(..., examples=["Quantum075"])
    from_broadcaster_user_login: str = Field(..., examples=["quantum075"])
    to_broadcaster_user_id: int = Field(...)
    to_broadcaster_user_name: str = Field(..., examples=["Quantum075"])
    to_broadcaster_user_login: str = Field(..., examples=["quantum075"])
    viewers: int = Field(...)


class RaidWebhookSchema(BaseModel):
    subscription: WebhookSubscriptionSchema[WebhookSubscriptionRaidConditionSchema]
    event: RaidWebhookEventSchema


class MessageFragmentCheermoteSchema(BaseModel):
    prefix: str
    bits: int
    tier: int


class MessageFragmentEmoteSchema(BaseModel):
    id: str
    emote_set_id: str
    owner_id: str
    format: list[str]


class MessageFragmentMentionSchema(BaseModel):
    user_id: str
    user_name: str
    user_login: str


class MessageFragmentSchema(BaseModel):
    type: str
    text: str
    cheermote: MessageFragmentCheermoteSchema | None
    emote: MessageFragmentEmoteSchema | None
    mention: MessageFragmentMentionSchema | None


class ChatMessageInnerSchema(BaseModel):
    text: str
    fragments: list[MessageFragmentSchema]


class ChatMessageBadge(BaseModel):
    set_id: str
    id: str
    info: str


class ChatMessageCheerMetadata(BaseModel):
    bits: int


class ChatMessageReplyMetadata(BaseModel):
    parent_message_id: str
    parent_message_body: str
    parent_user_id: str
    parent_user_name: str
    parent_user_login: str
    thread_message_id: str
    thread_user_id: str
    thread_user_name: str
    thread_user_login: str


class ChatMessageWebhookEventSchema(BaseModel):
    broadcaster_user_id: int = Field(...)
    broadcaster_user_login: str = Field(...)
    broadcaster_user_name: str = Field(...)
    chatter_user_id: int = Field(...)
    chatter_user_login: str = Field(...)
    chatter_user_name: str = Field(...)
    message_id: UUID = Field(...)
    message: ChatMessageInnerSchema = Field(...)
    color: str = Field(...)
    badges: list[ChatMessageBadge] = Field(...)
    cheer: ChatMessageCheerMetadata | None = Field(None)
    reply: ChatMessageReplyMetadata | None = Field(None)
    channel_points_custom_reward_id: str | None = Field(None)
    message_type: str = Field(...)
    source_broadcaster_user_id: int | None = Field(None)
    source_broadcaster_user_name: str | None = Field(None)
    source_broadcaster_user_login: str | None = Field(None)
    source_message_id: UUID | None = Field(None)
    source_badges: list[ChatMessageBadge] | None = Field(None)
    is_source_only: bool | None = Field(None)


class WebhookSubscriptionChatMessageConditionSchema(BaseModel):
    broadcaster_user_id: int = Field(...)
    user_id: int = Field(...)


class ChatMessageSchema(BaseModel):
    subscription: WebhookSubscriptionSchema[WebhookSubscriptionChatMessageConditionSchema]
    event: ChatMessageWebhookEventSchema
