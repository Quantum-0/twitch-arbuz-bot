from datetime import datetime, timedelta
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MemealertsSettings, TwitchUserSettings, User
from services.cache import Cache
from twitch.client.twitch import Twitch
from utils.streamers_sort import compute_streamer_score

# Окно «свежести» использования оверлеев: если последнее обращение к оверлею было
# позже этого порога, стример считается «использующим оверлеи».
OVERLAY_USAGE_FRESHNESS = timedelta(days=14)

SortKey = Literal["recommended", "followers", "created", "name"]
SortOrder = Literal["asc", "desc"]
TriState = bool | None

FILTER_KEYS = ("bot", "meme", "ai", "overlay", "online", "pants")


def _parse_tristate(value: str | None) -> TriState:
    if value is None or value == "":
        return None
    v = value.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


def _is_overlay_used_recently(overlays_last_usage: datetime | None, *, now: datetime | None = None) -> bool:
    if overlays_last_usage is None:
        return False
    now = now or datetime.now()
    return (now - overlays_last_usage) < OVERLAY_USAGE_FRESHNESS


def _build_select_query() -> sa.Select[tuple[Any, ...]]:
    return (
        sa.select(
            User.id.label("id"),
            User.login_name.label("username"),
            User.profile_image_url.label("avatar_url"),
            User.followers_count.label("followers"),
            User.in_beta_test.label("is_beta_tester"),
            User.donated.label("donated"),
            User.created_at.label("created_at"),
            User.interacted_at.label("interacted_at"),
            User.overlays_last_usage.label("overlays_last_usage"),
            TwitchUserSettings.enable_chat_bot.label("chat_bot_enabled"),
            TwitchUserSettings.enable_pants.label("pants_enabled"),
            TwitchUserSettings.ai_sticker_reward_id.is_not(None).label("ai_stickers_enabled"),
            MemealertsSettings.memealerts_reward.is_not(None).label("memealerts_enabled"),
        )
        .select_from(User)
        .join(TwitchUserSettings)
        .join(MemealertsSettings)
        .where(User.followers_count > 2)
        .limit(500)
    )


async def _resolve_online_streams(
    db_rows: list[dict[str, Any]], twitch: Twitch, cache: Cache
) -> set[str]:
    online_streams = await cache.get_set("online_streams")
    if online_streams:
        return online_streams
    if not db_rows:
        return set()
    streams = await twitch.get_streams([row["username"] for row in db_rows])
    online_streams = {row["username"] for row in db_rows if streams.get(row["username"])}
    await cache.set_set("online_streams", online_streams, ttl=300)
    return online_streams


def _resolve_role(row: dict[str, Any]) -> str | None:
    role: str | None = "beta" if row["is_beta_tester"] else None
    if row["username"] == "quantum075":
        role = "dev"
    if row["donated"] > 0:
        role = "donater"
    return role


_FILTER_FIELD_MAP = {
    "bot": "chat_bot_enabled",
    "meme": "memealerts_enabled",
    "ai": "ai_stickers_enabled",
    "overlay": "overlay_used_recently",
    "online": "is_live",
    "pants": "pants_enabled",
}


def _apply_filters(rows: list[dict[str, Any]], filters: dict[str, TriState]) -> list[dict[str, Any]]:
    result = rows
    for key, value in filters.items():
        if value is None:
            continue
        field = _FILTER_FIELD_MAP[key]
        result = [r for r in result if r[field] == value]
    return result


def _apply_sort(rows: list[dict[str, Any]], sort: SortKey, order: SortOrder) -> list[dict[str, Any]]:
    if sort == "recommended":
        rows.sort(key=compute_streamer_score, reverse=True)
        return rows
    reverse = order == "desc"
    if sort == "followers":
        rows.sort(key=lambda r: r["followers"] or 0, reverse=reverse)
    elif sort == "created":
        # Сортируем по id — он монотонно возрастает и точно отражает порядок регистрации
        # (created_at может совпадать у нескольких пользователей, созданных одновременно).
        rows.sort(key=lambda r: r["id"], reverse=reverse)
    elif sort == "name":
        rows.sort(key=lambda r: r["username"].lower(), reverse=reverse)
    return rows


async def get_streamers_list(
    db: AsyncSession,
    twitch: Twitch,
    cache: Cache,
    *,
    sort: SortKey = "recommended",
    order: SortOrder = "desc",
    filters: dict[str, TriState] | None = None,
) -> list[dict[str, Any]]:
    """Возвращает список стримеров с применёнными фильтрами и сортировкой.

    Поля возвращаемых dict-ов:
      username, avatar_url, followers, is_beta_tester, donated, created_at,
      interacted_at, overlays_last_usage, chat_bot_enabled, pants_enabled,
      ai_stickers_enabled, memealerts_enabled, is_live, score, role,
      overlay_used_recently.
    """
    res = [row._asdict() for row in (await db.execute(_build_select_query())).all()]

    online_streams = await _resolve_online_streams(res, twitch, cache)
    now = datetime.now()
    for row in res:
        row["is_live"] = row["username"] in online_streams
        row["overlay_used_recently"] = _is_overlay_used_recently(row["overlays_last_usage"], now=now)
        row["score"] = compute_streamer_score(row)
        row["role"] = _resolve_role(row)

    res = _apply_filters(res, filters or {})
    return _apply_sort(res, sort, order)


def public_streamer_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Сокращённый публичный payload для JSON-эндпоинта /api/user/streamers."""
    return {
        "username": row["username"],
        "avatar_url": row["avatar_url"],
        "followers": row["followers"],
        "is_live": row["is_live"],
        "role": row["role"],
    }
