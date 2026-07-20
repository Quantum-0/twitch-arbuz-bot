from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.enums import ChatbotDefaultTargetBehaviour, AIStickerModel, AIReferenceUsagePolicy


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
    enable_tiktok_link: bool | None = Field(None)
    enable_youtube_link: bool | None = Field(None)
    enable_memealerts_link: bool | None = Field(None)
    enable_links_command: bool | None = Field(None)

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

    ai_stickers_show_in_profile: bool | None = Field(None)
    ai_reference_show_in_profile: bool | None = Field(None)
    ai_sticker_model: AIStickerModel | None = Field(None)
    ai_reference_usage_policy: AIReferenceUsagePolicy | None = Field(None)
    ai_reference_allow_on_other_channels: bool | None = Field(None)


class UpdateMemealertsCoinsSchema(BaseModel):
    count: int = Field(..., ge=1, le=100)


class BoolResponseSchema(BaseModel):
    result: bool


class CheckStatusResponseSchema(BoolResponseSchema):
    problems: list[str]


class CheckMemealertsRewardStatusResponseSchema(CheckStatusResponseSchema):
    state: Literal["missing"] | Literal["broken"] | Literal["ok"]


class UUIDResponseSchema(BaseModel):
    id_: UUID = Field(alias="id")


class BaseErrorSchema(BaseModel):
    detail: str = Field(examples=["Error description"])


class StatsType(StrEnum):
    """Типы метрик, собираемых подсистемой статистики."""

    MESSAGE_INCOMING = "message_incoming"
    MESSAGE_OUTGOING = "message_outgoing"
    REWARD_MEMECOINS = "reward_memecoins"
    REWARD_AI_STICKERS = "reward_ai_stickers"
    COMMAND_HANDLED = "command_handled"
    # Timing-метрика: avg время (мс) от on_message до первого send_message.
    # В таблице: count — число замеров, sum_ms — суммарное время.
    MESSAGE_PROCESSING_TIME = "message_processing_time"
    # Count-метрика: число уникальных каналов, на которых были входящие/исходящие
    # сообщения за бакет. В таблице: count = размер сета channel_id, sum_ms=0.
    # Subtype: "incoming" | "outgoing".
    ACTIVE_CHANNELS = "active_channels"


class StatsPeriod(StrEnum):
    """Период агрегации данных для графика."""

    TEN_MIN = "10m"
    ONE_HOUR = "1h"
    THREE_HOURS = "3h"
    SIX_HOURS = "6h"
    ONE_DAY = "1d"


class StatsPointSchema(BaseModel):
    """Одна точка графика: начало бакета (UTC) и агрегированное значение."""

    datetime: datetime
    value: int


class StatsResponseSchema(BaseModel):
    """Ответ ручки /api/user/stats: ряд точек для графика."""

    type: str
    subtype: str | None = None
    period: str
    points: list[StatsPointSchema]


class StatsSeriesPointSchema(BaseModel):
    """Одна точка multi-line графика (для ручки /api/user/stats/series)."""

    datetime: datetime
    value: int


class StatsSeriesItemSchema(BaseModel):
    """Один ряд (подтип) multi-line графика."""

    subtype: str
    points: list[StatsSeriesPointSchema]


class StatsSeriesResponseSchema(BaseModel):
    """Ответ ручки /api/user/stats/series: топ-N подтипов с рядами точек."""

    type: str
    period: str
    series: list[StatsSeriesItemSchema]
