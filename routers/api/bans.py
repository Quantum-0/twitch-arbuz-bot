"""API управления банами (``/api/bans``).

Права:
- **Стафф** (``utils.staff.is_staff``): просмотр всех банов (global + чужие
  канальные), выдача/снятие **global**-банов. Канальные баны чужих каналов —
  только просмотр; свои канальные баны снимает как обычный стример.
- **Стример**: просмотр и управление банами только своего канала.

Особенности global-бана: если таргет зарегистрирован как пользователь сайта —
выдаётся полный бан (все фичи + ``site``, редирект на ``/banned``); зрителю
без аккаунта бан выдаётся на запрошенные фичи.
"""

import logging
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Security

from container import Container
from database.models import Ban, User
from routers.security_helpers import user_auth
from schemas.ban_schemas import BanCreatedSchema, BanCreateSchema, BanOutSchema, BansListSchema
from schemas.enums import FULL_BAN_FEATURES, VIEWER_BAN_FEATURES, BanFeature, BanScope
from services.bans import BanService
from services.cache import Cache
from twitch.client.twitch import Twitch
from utils.chat_roles import KNOWN_BOTS
from utils.reference_moderation import is_reference_admin, is_reference_moderator
from utils.staff import is_staff

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bans", tags=["Bans"])

# Rate limit выдачи банов: каждый POST резолвит логин через Helix от общего
# bot-токена — без лимита один стример может исчерпать квоты всего сервиса.
_BAN_CREATE_RATE_LIMIT = 10
_BAN_CREATE_RATE_WINDOW_S = 60


def _to_out(ban: Ban, *, can_manage: bool) -> BanOutSchema:
    return BanOutSchema(
        id=ban.id,
        target_twitch_id=ban.target_twitch_id,
        target_login=ban.target_login,
        target_name=ban.target_name,
        scope=BanScope(ban.scope),
        channel_twitch_id=ban.channel_twitch_id,
        channel_login=ban.channel_login,
        features=[BanFeature(f) for f in ban.features],
        reason=ban.reason,
        created_by_login=ban.created_by_login,
        created_by_role=ban.created_by_role,
        created_at=ban.created_at,
        expires_at=ban.expires_at,
        can_manage=can_manage,
    )


def _can_manage(ban: Ban, user: User, staff: bool) -> bool:
    if ban.scope == BanScope.GLOBAL.value:
        return staff
    return ban.channel_twitch_id == user.twitch_id


@router.get("")
@inject
async def list_bans(
    bans_service: Annotated[BanService, Depends(Provide[Container.bans])],
    user: Annotated[User, Security(user_auth)],
) -> BansListSchema:
    staff = is_staff(user)
    # Истёкшие включаем: они инертны, но строку видно и можно удалить.
    if staff:
        rows = await bans_service.list_bans(include_expired=True)
    else:
        rows = await bans_service.list_bans(
            scope=BanScope.CHANNEL, channel_twitch_id=user.twitch_id, include_expired=True
        )
    return BansListSchema(
        bans=[_to_out(b, can_manage=_can_manage(b, user, staff)) for b in rows],
        staff=staff,
        my_channel_twitch_id=user.twitch_id,
        my_channel_login=user.login_name,
    )


@router.post("")
@inject
async def create_ban(
    payload: BanCreateSchema,
    bans_service: Annotated[BanService, Depends(Provide[Container.bans])],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
    user: Annotated[User, Security(user_auth)],
) -> BanCreatedSchema:
    staff = is_staff(user)

    if not await cache.check_rate_limit(
        f"bans_create:{user.twitch_id}", _BAN_CREATE_RATE_LIMIT, _BAN_CREATE_RATE_WINDOW_S
    ):
        raise HTTPException(status_code=429, detail="Слишком много банов подряд — попробуйте через минуту")
    if payload.scope == BanScope.GLOBAL and not staff:
        raise HTTPException(status_code=403, detail="Global bans are staff-only")
    if payload.login.strip("@").lower() in KNOWN_BOTS:
        raise HTTPException(status_code=400, detail="Нельзя забанить бота")

    target = await twitch.get_bot_user_by_login(payload.login)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Twitch user `{payload.login}` not found")
    if str(target.id) == user.twitch_id:
        raise HTTPException(status_code=400, detail="Нельзя забанить самого себя")

    # Сотрудникам сервиса (модераторам/админам) нельзя ограничить доступ к сайту —
    # иначе забаненный модератор теряет и доступ к управлению банами.
    target_is_staff = is_reference_moderator(target.login)
    if target_is_staff and BanFeature.SITE in payload.features:
        raise HTTPException(status_code=403, detail="Нельзя ограничить доступ к сайту сотрудникам сервиса")

    full_ban = False
    features = sorted(set(payload.features), key=lambda f: f.value)
    if payload.scope == BanScope.GLOBAL:
        # Зарегистрированный пользователь (стример) → полный бан автоматически.
        # Исключение — сотрудники сервиса: только запрошенные фичи, без site.
        if not target_is_staff and await bans_service.is_registered_user(str(target.id)):
            features = sorted(FULL_BAN_FEATURES, key=lambda f: f.value)
            full_ban = True
        elif not features:
            raise HTTPException(status_code=422, detail="Укажите хотя бы одну ограничиваемую функцию")
    else:
        # Канальный бан не может ограничивать доступ к сайту; по умолчанию — все
        # зрительские фичи.
        features = [f for f in features if f != BanFeature.SITE] or sorted(VIEWER_BAN_FEATURES, key=lambda f: f.value)

    ban = await bans_service.issue(
        target_twitch_id=str(target.id),
        target_login=target.login,
        target_name=target.display_name,
        features=features,
        scope=payload.scope,
        channel_twitch_id=user.twitch_id if payload.scope == BanScope.CHANNEL else None,
        channel_login=user.login_name if payload.scope == BanScope.CHANNEL else None,
        reason=payload.reason,
        expires_at=payload.expires_at,
        created_by_twitch_id=user.twitch_id,
        created_by_login=user.login_name,
        created_by_role=(
            ("admin" if is_reference_admin(user.login_name) else "moderator")
            if staff and payload.scope == BanScope.GLOBAL
            else "streamer"
        ),
    )
    return BanCreatedSchema(ban=_to_out(ban, can_manage=True), full_ban=full_ban)


@router.delete("/{ban_id}")
@inject
async def revoke_ban(
    ban_id: int,
    bans_service: Annotated[BanService, Depends(Provide[Container.bans])],
    user: Annotated[User, Security(user_auth)],
) -> BanOutSchema:
    staff = is_staff(user)
    ban = await bans_service.get_ban(ban_id, include_expired=True)
    if ban is None:
        raise HTTPException(status_code=404, detail="Ban not found")
    if not _can_manage(ban, user, staff):
        raise HTTPException(
            status_code=403,
            detail="Канальные баны управляются владельцем канала, global — стаффом сервиса",
        )
    revoked = await bans_service.revoke(ban_id)
    if revoked is None:
        raise HTTPException(status_code=404, detail="Ban not found")
    return _to_out(revoked, can_manage=True)
