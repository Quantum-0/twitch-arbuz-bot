"""Pydantic-схемы API подсистемы банов (``/api/bans``)."""

from datetime import datetime

from pydantic import BaseModel, Field

from schemas.enums import BanFeature, BanScope


class BanCreateSchema(BaseModel):
    """Выдача бана.

    Для ``scope=global`` и незарегистрированного таргета — точечные ``features``;
    если таргет является пользователем сайта (стримером), сервер игнорирует
    ``features`` и выдаёт полный бан (все фичи + site).
    Для ``scope=channel`` бан всегда накладывается на канал самого запросившего.
    """

    login: str = Field(min_length=1, max_length=64, examples=["Vasya"])
    scope: BanScope = Field(default=BanScope.CHANNEL)
    features: list[BanFeature] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=512)
    expires_at: datetime | None = None


class BanOutSchema(BaseModel):
    """Бан в списке. ``can_manage`` — может ли текущий пользователь снять этот бан."""

    id: int
    target_twitch_id: str
    target_login: str
    target_name: str
    scope: BanScope
    channel_twitch_id: str | None = None
    channel_login: str | None = None
    features: list[BanFeature]
    reason: str | None = None
    created_by_login: str
    created_by_role: str
    created_at: datetime
    expires_at: datetime | None = None
    can_manage: bool = False


class BansListSchema(BaseModel):
    bans: list[BanOutSchema]
    staff: bool = False
    my_channel_twitch_id: str
    my_channel_login: str


class BanCreatedSchema(BaseModel):
    ban: BanOutSchema
    full_ban: bool = Field(description="Таргет — пользователь сайта: выдан полный бан (все фичи + site).")
