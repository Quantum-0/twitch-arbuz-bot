from datetime import datetime
from uuid import UUID

from pydantic import AnyHttpUrl
from pydantic import BaseModel, Field

from config import settings


class UpdateSettingsForm(BaseModel):
    enable_chat_bot: bool | None = Field(None)

    enable_bite: bool | None = Field(None)
    enable_lick: bool | None = Field(None)
    enable_boop: bool | None = Field(None)
    enable_pat: bool | None = Field(None)
    enable_hug: bool | None = Field(None)

    enable_banana: bool | None = Field(None)
    enable_whoami: bool | None = Field(None)
    enable_lurk: bool | None = Field(None)

    enable_riot: bool | None = Field(None)
    enable_pants: bool | None = Field(None)

    enable_pyramid: bool | None = Field(None)
    enable_pyramid_breaker: bool | None = Field(None)



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


class WebhookSubscriptionConditionSchema(BaseModel):
    reward_id: UUID = Field(...)
    broadcaster_user_id: int = Field(...)


class WebhookTransportSchema(BaseModel):
    method: str = Field(..., examples=["webhook"])
    callback: AnyHttpUrl = Field(..., examples=[str(settings.reward_redemption_webhook) + "/123"])


class WebhookSubscriptionSchema(BaseModel):
    subscription_id: UUID = Field(..., alias="id")
    cost: int = Field(...)
    type: str = Field(..., examples=["channel.channel_points_custom_reward_redemption.add"])
    status: str = Field(..., examples=["enabled"])
    version: int = Field(..., examples=[1])
    condition: WebhookSubscriptionConditionSchema
    transport: WebhookTransportSchema
    created_at: datetime = Field(...)


class PointRewardRedemptionWebhookSchema(BaseModel):
    event: PointRewardRedemptionWebhookEventSchema
    subscription: WebhookSubscriptionSchema

class TwitchChallengeSchema(BaseModel):
    challenge: str