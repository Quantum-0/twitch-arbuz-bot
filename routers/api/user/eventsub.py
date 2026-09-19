"""Generic API для управления EventSub-подписками оверлеев.

POST /api/user/eventsub — создать подписки (auth: X-Overlay-Secret).
DELETE /api/user/eventsub — удалить подписки (auth: X-Overlay-Secret).

Оверлеи (например halloween) вызывают POST при загрузке и регулярно (heartbeat),
сервер хранит список активных типов в Redis. Если SSE-клиенты отсутствуют
дольше N минут — cleanup-job автоматически удаляет подписки.
"""

import json
import logging
from typing import Annotated

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from container import Container
from database.models import User
from dependencies import get_db
from services.cache import Cache
from twitch.client.twitch import Twitch
from utils.overlay_secret import ensure_overlay_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/eventsub", tags=["EventSub overlay management"])

# TTL записи в Redis — сколько подписки считаются «активными» после последнего
# heartbeat от оверлея. Cleanup-job проверяет истёкшие записи.
OVERLAY_EVENTSUB_TTL_S = 10 * 60

# Типы подписок, которые можно создавать через этот endpoint.
SUPPORTED_TYPES: set[str] = {
    "channel.follow",
    "channel.subscribe",
    "channel.subscription.message",
    "channel.raid",
}

REDIS_KEY_PREFIX = "eventsub:overlay:"
REDIS_USERS_SET = "eventsub:overlay:users"


class EventSubRequest(BaseModel):
    channel_id: int
    types: list[str] = Field(min_length=1)


async def _get_user_by_secret(db: AsyncSession, request: Request, channel_id: int) -> User:
    secret_header = request.headers.get("X-Overlay-Secret")
    if not secret_header:
        raise HTTPException(401, "No overlay secret provided")

    user = (await db.execute(sa.select(User).where(User.twitch_id == str(channel_id)))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    secret = await ensure_overlay_secret(db, user)
    if secret_header != str(secret):
        raise HTTPException(403, "Invalid overlay secret")
    return user


async def _get_existing_sub_types(twitch: Twitch, user: User, wanted_types: list[str]) -> set[str]:
    """Найти какие из wanted_types уже подписаны для пользователя."""
    existing_subs = await twitch.get_subscriptions()
    existing_types: set[str] = set()
    for sub in existing_subs:
        cond = sub.condition
        broadcaster_id = cond.get("broadcaster_user_id") or cond.get("to_broadcaster_user_id")
        if broadcaster_id == str(user.twitch_id) and sub.type in wanted_types:
            existing_types.add(sub.type)
    return existing_types


_SUBSCRIBE_METHODS = {
    "channel.follow": "subscribe_follow",
    "channel.subscribe": "subscribe_subscribe",
    "channel.subscription.message": "subscribe_subscription_message",
    "channel.raid": "subscribe_raid",
}


async def _create_missing_subs(
    twitch: Twitch,
    user: User,
    wanted_types: list[str],
    existing_types: set[str],
) -> tuple[list[str], list[dict[str, str]]]:
    """Создать недостающие подписки. Возвращает (created, errors)."""
    created: list[str] = []
    errors: list[dict[str, str]] = []
    for sub_type in wanted_types:
        if sub_type in existing_types:
            continue
        method_name = _SUBSCRIBE_METHODS.get(sub_type)
        if not method_name:
            continue
        try:
            await getattr(twitch, method_name)(user)
            created.append(sub_type)
        except Exception as exc:
            logger.warning("Failed to subscribe %s for user %s: %s", sub_type, user.login_name, exc)
            errors.append({"type": sub_type, "error": str(exc)})
    return created, errors


@router.post("")
@inject
async def create_eventsub_subscriptions(
    request: Request,
    payload: Annotated[EventSubRequest, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
):
    """Создать EventSub-подписки для канала.

    Вызывается оверлеем (через X-Overlay-Secret) при загрузке и heartbeat.
    Создаёт только недостающие подписки, существующие не трогает.
    Записывает список типов в Redis с TTL.
    """
    unsupported = set(payload.types) - SUPPORTED_TYPES
    if unsupported:
        raise HTTPException(400, f"Unsupported subscription types: {unsupported}")

    user = await _get_user_by_secret(db, request, payload.channel_id)

    key = f"{REDIS_KEY_PREFIX}{user.twitch_id}"
    await cache.set_str(key, json.dumps(payload.types), ttl=OVERLAY_EVENTSUB_TTL_S)

    existing_users_raw = await cache.get_set(REDIS_USERS_SET)
    existing_users = {u.decode() if isinstance(u, bytes) else u for u in existing_users_raw}
    existing_users.add(str(user.twitch_id))
    await cache.set_set(REDIS_USERS_SET, existing_users, ttl=24 * 60 * 60)

    existing_types = await _get_existing_sub_types(twitch, user, payload.types)
    created, errors = await _create_missing_subs(twitch, user, payload.types, existing_types)

    return JSONResponse({"created": created, "existing": list(existing_types), "errors": errors}, 200)


@router.delete("")
@inject
async def delete_eventsub_subscriptions(
    request: Request,
    payload: Annotated[EventSubRequest, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
):
    """Удалить EventSub-подписки для канала.

    Удаляет только подписки из payload.types. Raid удаляется только если
    enable_shoutout_on_raid выключен — иначе она принадлежит shoutout-функции.
    """
    user = await _get_user_by_secret(db, request, payload.channel_id)

    deleted: list[str] = []
    errors: list[dict[str, str]] = []

    for sub_type in payload.types:
        if sub_type not in SUPPORTED_TYPES:
            errors.append({"type": sub_type, "error": "Unsupported type"})
            continue

        if sub_type == "channel.raid" and user.settings.enable_shoutout_on_raid:
            continue

        try:
            await twitch.unsubscribe_by_type(user, sub_type)
            deleted.append(sub_type)
        except Exception as exc:
            logger.warning("Failed to unsubscribe %s for user %s: %s", sub_type, user.login_name, exc)
            errors.append({"type": sub_type, "error": str(exc)})

    # Удаляем Redis-запись.
    key = f"{REDIS_KEY_PREFIX}{user.twitch_id}"
    await cache.set_str(key, "[]", ttl=1)

    return JSONResponse({"deleted": deleted, "errors": errors}, 200)
