from datetime import datetime
from enum import StrEnum
from typing import Generic, Literal, TypeVar, Annotated, Union
from uuid import UUID, uuid3

from pydantic import AnyHttpUrl, BaseModel, Field

from config import settings


class UpdateSettingsForm(BaseModel):
    enable_chat_bot: bool | None = Field(None)

    enable_bite: bool | None = Field(None)
    enable_lick: bool | None = Field(None)
    enable_boop: bool | None = Field(None)
    enable_pat: bool | None = Field(None)
    enable_hug: bool | None = Field(None)
    enable_bonk: bool | None = Field(None)
    enable_feed: bool | None = Field(None)

    enable_dice: bool | None = Field(None)
    enable_pasta: bool | None = Field(None)

    enable_tg_link: bool | None = Field(None)
    enable_ds_link: bool | None = Field(None)

    enable_banana: bool | None = Field(None)
    enable_treat: bool | None = Field(None)
    enable_whoami: bool | None = Field(None)
    enable_lurk: bool | None = Field(None)
    enable_horny_good: bool | None = Field(None)
    enable_horny_bad: bool | None = Field(None)
    enable_tail: bool | None = Field(None)

    enable_riot: bool | None = Field(None)
    enable_pants: bool | None = Field(None)

    enable_pyramid: bool | None = Field(None)
    enable_pyramid_breaker: bool | None = Field(None)

    enable_shoutout_on_raid: bool | None = Field(None)


class UpdateMemealertsCoinsSchema(BaseModel):
    count: int = Field(..., ge=1, le=100)


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
    callback: AnyHttpUrl = Field(
        ..., examples=[str(settings.reward_redemption_webhook) + "/123"]
    )


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
    subscription: WebhookSubscriptionSchema[
        WebhookSubscriptionRewardRedemptionConditionSchema
    ]


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
    subscription: WebhookSubscriptionSchema[
        WebhookSubscriptionChatMessageConditionSchema
    ]
    event: ChatMessageWebhookEventSchema


class BoolResponseSchema(BaseModel):
    result: bool

# TODO: рефактор схем: схемы в отдельной папке, internal, api, twitch-eventsub, integration?

# {"channel":"quantum075","event":"game-new","data":{"challenge_id":"c66acc72-258f-4bd5-823d-23fadc70ab6a","secret_word":"медь"}}
#
# {"channel":"quantum075","event":"game-win","data":{"winner":{"login":"quantum075","display_name":"Quantum075"},"winning_word":"медь","attempts_used":2,"unique_words":2,"repeated_words":0,"round_duration_sec":37}}
#
# {"channel":"quantum075","event":"game-tip","data":{"tip_word":"водолаз","tip_distance":150,"challenge_id":"88d617f7-cb77-456f-8871-6f22c642fea9"}}

class SlovotronNewGameDataSchema(BaseModel):
    challenge_id: UUID
    secret_word: str

class SlovotronWinDataSchema(BaseModel):
    class SlovotronWinnerSchema(BaseModel):
        login: str
        display_name: str
    winner: SlovotronWinnerSchema
    winning_word: str
    attempts_used: int
    unique_words: int
    repeated_words: int
    round_duration_sec: int

class SlovotronTipDataSchema(BaseModel):
    tip_word: str
    tip_distance: int
    challenge_id: UUID

class SlovotronEvent(StrEnum):
    GAME_NEW = "game-new"
    GAME_TIP = "game-tip"
    GAME_WIN = "game-win"

class SlovotronWebhookBaseSchema(BaseModel):
    channel: str
    secret: UUID

    def validate_secret(self):
        return self.secret == uuid3(namespace=settings.slovotron_secret, name=self.channel)

class SlovotronNewWebhookSchema(SlovotronWebhookBaseSchema):
    event: Literal["game-new"]  # Literal обязателен для дискриминатора
    data: SlovotronNewGameDataSchema

class SlovotronWinWebhookSchema(SlovotronWebhookBaseSchema):
    event: Literal["game-win"]
    data: SlovotronWinDataSchema

class SlovotronTipWebhookSchema(SlovotronWebhookBaseSchema):
    event: Literal["game-tip"]
    data: SlovotronTipDataSchema

SlovotronWebhookSchema = Annotated[
    Union[SlovotronNewWebhookSchema, SlovotronWinWebhookSchema, SlovotronTipWebhookSchema],
    Field(discriminator='event')
]
