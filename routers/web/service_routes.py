import asyncio
import logging
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter
from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse

from config import settings
from container import Container
from database.models import User
from dependencies import get_db
from twitch.client.twitch import Twitch

router = APIRouter(prefix="", tags=["Service"])


@router.get("/favicon.ico", response_class=FileResponse)
async def favicon():
    return FileResponse("static/favicon.ico", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/login", response_class=RedirectResponse)
async def login():
    return RedirectResponse(settings.login_twitch_url)


@router.get("/login-callback", response_class=RedirectResponse)
@inject
async def callback(
    request: Request,
    code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
):
    tokens = await twitch.get_user_access_refresh_tokens_by_authorization_code(code)
    if tokens is None:
        return RedirectResponse(url="/")

    (
        access_token,
        refresh_token,
    ) = tokens
    user_info = await twitch.get_self(access_token, refresh_token)

    user_id = user_info.id
    login_name = user_info.login
    profile_image_url = user_info.profile_image_url

    result = await db.execute(sa.select(User).filter_by(twitch_id=user_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            twitch_id=user_id,
            login_name=login_name,
            profile_image_url=profile_image_url,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        db.add(user)
    else:
        user.access_token = access_token
        user.refresh_token = refresh_token
        user.profile_image_url = profile_image_url
        user.login_name = login_name
        db.add(user)

    await db.commit()
    asyncio.create_task(login_callback_task(user))

    request.session["user_id"] = user_id
    return RedirectResponse(url="/panel")


logger = logging.getLogger(__name__)


# TODO: сделать бэкграунд таской штоле о_О
@inject
async def login_callback_task(
    user: User,
    twitch: Annotated[Twitch, Provide[Container.twitch]],
    db_session_factory: Annotated[Callable[[], AsyncSession], Provide[Container.db_session_factory]],
):
    # Получаем фолловеров
    followers = await twitch.get_followers(user)

    # Делаем бота модератором (ТОЛЬКО ПРИ ЛОГИНЕ)
    await twitch.set_bot_moder(user)

    async with db_session_factory() as session:
        # Обновляем фолловеров
        q = sa.update(User).values(followers_count=followers.total).where(User.twitch_id == user.twitch_id)
        await session.execute(q)
        await session.commit()

        # Получаем настройки мемалёртов, рейдов и прочих ревардов
        res = await session.execute(
            sa.select(User)
            .options(
                selectinload(User.settings),
                selectinload(User.memealerts),
            )
            .filter_by(twitch_id=user.twitch_id)
        )
        user = res.scalar_one_or_none()  # type: ignore
        shoutout_to_raid_is_enabled = user.settings.enable_shoutout_on_raid
        memealerts_reward = user.memealerts.memealerts_reward
        ai_stickers_reward = user.settings.ai_sticker_reward_id

    if shoutout_to_raid_is_enabled is False and memealerts_reward is None and ai_stickers_reward is None:
        return

    subs = await twitch.get_subscriptions()

    subs_for_rewards = [
        sub
        for sub in subs
        if sub.type == "channel.channel_points_custom_reward_redemption.add"
        and sub.condition.get("broadcaster_user_id") == user.twitch_id
    ]

    subs_for_raid = [
        sub
        for sub in subs
        if sub.type == "channel.raid" and sub.condition.get("to_broadcaster_user_id") == user.twitch_id
    ]

    # TODO: unsubscribe from unused subs

    if shoutout_to_raid_is_enabled and not subs_for_raid:
        logger.warning(f"Found missing raid eventsub for user `{user}`. Re-subscribed!")
        await twitch.subscribe_raid(user)
    elif not shoutout_to_raid_is_enabled and subs_for_raid:
        await twitch.unsubscribe_raid(subscription_id=UUID(subs_for_raid[0].id))

    for sub in subs_for_rewards:
        if sub.condition.get("reward_id") not in {str(ai_stickers_reward), str(memealerts_reward)}:
            logger.warning(f"Found extra eventsubs for user `{user}`. Unsubscribing..")
            await twitch.unsubscribe_event_sub(sub.id)

    sub_for_memecoins_exist = any(
        sub for sub in subs_for_rewards if sub.condition.get("reward_id") == str(memealerts_reward)
    )
    sub_for_ai_stickers_exist = any(
        sub for sub in subs_for_rewards if sub.condition.get("reward_id") == str(ai_stickers_reward)
    )

    if memealerts_reward and not sub_for_memecoins_exist:
        logger.warning(f"Found missing memealerts reward eventsub for user `{user}`. Re-subscribed!")
        await twitch.subscribe_reward(user, memealerts_reward)

    if ai_stickers_reward and not sub_for_ai_stickers_exist:
        logger.warning(f"Found missing ai sticker reward eventsub for user `{user}`. Re-subscribed!")
        await twitch.subscribe_reward(user, ai_stickers_reward)

    logger.info(f"Post-login validation for user {user} is done!")
