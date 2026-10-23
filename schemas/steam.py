"""Pydantic-схемы для Steam Web API (ISteamUser/GetPlayerSummaries)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SteamPlayerSummary(BaseModel):
    """Профиль игрока из ISteamUser/GetPlayerSummaries/v0002.

    Все поля, кроме ``steamid``/``personaname``, Steam может не вернуть
    (приватный профиль, устаревший клиент и т.п.) — поэтому они опциональны.
    """

    steamid: str
    communityvisibilitystate: int = 1
    profilestate: int | None = None
    personaname: str
    commentpermission: int | None = None
    profileurl: str | None = None
    avatar: str | None = None
    avatarmedium: str | None = None
    avatarfull: str | None = None
    avatarhash: str | None = None
    lastlogoff: int | None = None
    personastate: int = 0
    realname: str | None = None
    primaryclanid: str | None = None
    timecreated: int | None = None
    personastateflags: int = 0
    loccountrycode: str | None = None
    locstatecode: str | None = None
    loccityid: int | None = None


class SteamPlayersResponse(BaseModel):
    """Обёртка ``{"response": {"players": [...]}}``."""

    players: list[SteamPlayerSummary] = Field(default_factory=list)


class SteamPlayerSummariesResponse(BaseModel):
    """Корневой ответ GetPlayerSummaries."""

    response: SteamPlayersResponse
