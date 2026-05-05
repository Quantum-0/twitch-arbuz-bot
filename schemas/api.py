from pydantic import BaseModel, Field

from schemas.enums import ChatbotDefaultTargetBehaviour


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

    # Extra
    allow_shared_chat: bool | None = Field(None)
    chatbot_default_target_behaviour: ChatbotDefaultTargetBehaviour | None = Field(None)


class UpdateMemealertsCoinsSchema(BaseModel):
    count: int = Field(..., ge=1, le=100)


class BoolResponseSchema(BaseModel):
    result: bool
