from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MemealertsDeposit, MemealertsSettings, TwitchUserSettings, User, UserLike
from schemas.enums import MemealertsDepositStatus
from services.cache import Cache
from twitch.client.twitch import Twitch
from utils.streamers_sort import compute_streamer_score

# Окно «свежести» использования оверлеев: если последнее обращение к оверлею было
# позже этого порога, стример считается «использующим оверлеи».
OVERLAY_USAGE_FRESHNESS = timedelta(days=14)

SortKey = Literal["recommended", "followers", "created", "name", "interacted", "likes"]
SortOrder = Literal["asc", "desc"]
TriState = bool | None

FILTER_KEYS = ("bot", "meme", "ai", "overlay", "online", "pants", "shoutout")


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
    likes = (
        sa.select(UserLike.to_user_id, sa.func.count(UserLike.from_user_id).label("likes_count"))
        .group_by(UserLike.to_user_id)
        .subquery()
    )

    # Метрики награды мемкоинов для скоринга рекомендаций: только успешные
    # пополнения. Накопительные метрики — скользящие 90 дней (отражают текущее
    # состояние, а не исторический след), недельная — 7 дней. created_at в
    # таблице naive-UTC, поэтому cutoff тоже собирается как naive-UTC.
    now = datetime.now(UTC).replace(tzinfo=None)
    succeeded = MemealertsDeposit.status == MemealertsDepositStatus.SUCCEEDED
    since_90d = now - timedelta(days=90)
    since_7d = now - timedelta(days=7)

    deposits_90d = (
        sa.select(
            MemealertsDeposit.twitch_id.label("twitch_id"),
            sa.func.count().label("deposits_count"),
            sa.func.count(sa.func.distinct(MemealertsDeposit.chatter_twitch_id)).label("deposits_users"),
        )
        .where(succeeded, MemealertsDeposit.created_at >= since_90d)
        .group_by(MemealertsDeposit.twitch_id)
        .subquery()
    )
    # Самый активный зритель канала (для анти-халявы в скоринге): max по
    # группировке (канал, зритель).
    deposits_per_chatter = (
        sa.select(
            MemealertsDeposit.twitch_id.label("twitch_id"),
            sa.func.count().label("chatter_count"),
        )
        .where(succeeded, MemealertsDeposit.created_at >= since_90d)
        .group_by(MemealertsDeposit.twitch_id, MemealertsDeposit.chatter_twitch_id)
        .subquery()
    )
    top_chatter = (
        sa.select(
            deposits_per_chatter.c.twitch_id.label("twitch_id"),
            sa.func.max(deposits_per_chatter.c.chatter_count).label("deposits_top1_count"),
        )
        .group_by(deposits_per_chatter.c.twitch_id)
        .subquery()
    )
    deposits_weekly = (
        sa.select(
            MemealertsDeposit.twitch_id.label("twitch_id"),
            sa.func.coalesce(sa.func.sum(MemealertsDeposit.memecoins), 0).label("deposits_weekly_coins"),
            sa.func.count(sa.func.distinct(MemealertsDeposit.chatter_twitch_id)).label("deposits_weekly_users"),
        )
        .where(succeeded, MemealertsDeposit.created_at >= since_7d)
        .group_by(MemealertsDeposit.twitch_id)
        .subquery()
    )

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
            TwitchUserSettings.enable_shoutout_on_raid.label("shoutout_enabled"),
            TwitchUserSettings.ai_sticker_reward_id.is_not(None).label("ai_stickers_enabled"),
            MemealertsSettings.memealerts_reward.is_not(None).label("memealerts_enabled"),
            sa.func.coalesce(likes.c.likes_count, 0).label("likes_count"),
            sa.func.coalesce(deposits_90d.c.deposits_count, 0).label("deposits_count"),
            sa.func.coalesce(deposits_90d.c.deposits_users, 0).label("deposits_users"),
            sa.func.coalesce(top_chatter.c.deposits_top1_count, 0).label("deposits_top1_count"),
            sa.func.coalesce(deposits_weekly.c.deposits_weekly_coins, 0).label("deposits_weekly_coins"),
            sa.func.coalesce(deposits_weekly.c.deposits_weekly_users, 0).label("deposits_weekly_users"),
        )
        .select_from(User)
        .join(TwitchUserSettings)
        .join(MemealertsSettings)
        .outerjoin(likes, likes.c.to_user_id == User.id)
        .outerjoin(deposits_90d, deposits_90d.c.twitch_id == User.twitch_id)
        .outerjoin(top_chatter, top_chatter.c.twitch_id == User.twitch_id)
        .outerjoin(deposits_weekly, deposits_weekly.c.twitch_id == User.twitch_id)
        .where(User.followers_count > 2)
        .limit(500)
    )


async def _resolve_online_streams(db_rows: list[dict[str, Any]], twitch: Twitch, cache: Cache) -> set[str]:
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
    "shoutout": "shoutout_enabled",
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
    elif sort == "interacted":
        # Сортировка по последнему взаимодействию с сервисом (interacted_at).
        rows.sort(key=lambda r: r["interacted_at"], reverse=reverse)
    elif sort == "name":
        rows.sort(key=lambda r: r["username"].lower(), reverse=reverse)
    elif sort == "likes":
        rows.sort(key=lambda r: r["username"].lower())
        rows.sort(key=lambda r: r["likes_count"], reverse=reverse)
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
      overlay_used_recently, likes_count, deposits_count, deposits_users,
      deposits_top1_count, deposits_weekly_coins, deposits_weekly_users
      (deposits_* — агрегаты memealerts_deposits для скоринга рекомендаций).
    """
    res = [row._asdict() for row in (await db.execute(_build_select_query())).all()]

    online_streams = await _resolve_online_streams(res, twitch, cache)
    now = datetime.now()
    for row in res:
        row["is_live"] = row["username"] in online_streams
        row["overlay_used_recently"] = _is_overlay_used_recently(row["overlays_last_usage"], now=now)
        row["score"] = compute_streamer_score(row)
        row["role"] = _resolve_role(row)
        row["avatar_url"] = row.get("avatar_url")[::-1].replace("003x003", "07x07", 1)[::-1]
        # разворачиваем чтоб только последнее с конца заменить (мало ли в середине будет о_О)

    res = _apply_filters(res, filters or {})  # TODO: эти фильтры должны применяться в запросе в бд, а не после него!!!
    # хотяяя.. есть шанс тогда поломать кэш.. короче надо подумать О.о
    return _apply_sort(res, sort, order)


def public_streamer_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Сокращённый публичный payload для JSON-эндпоинта /api/user/streamers."""
    return {
        "username": row["username"],
        "avatar_url": row["avatar_url"],
        "followers": row["followers"],
        "is_live": row["is_live"],
        "role": row["role"],
        "likes_count": row["likes_count"],
    }
