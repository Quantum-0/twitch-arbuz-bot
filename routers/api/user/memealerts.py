from collections.abc import Callable
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Security, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from twitchAPI.type import TwitchAPIException

from container import Container
from database.models import User, MemealertsSettings
from routers.security_helpers import user_auth
from services.memes_v2 import MemealertsOAuthService
from twitch.client.twitch import Twitch
import sqlalchemy as sa

router = APIRouter(prefix="/memealerts", tags=["Memealerts"])


@router.delete("", response_class=JSONResponse)
@inject
async def delete_ma_tokens(
    memealerts: Annotated[MemealertsOAuthService, Depends(Provide[Container.memealerts_auth])],
    user: User = Security(user_auth),
):
    await memealerts.delete_token(user)
    return JSONResponse(
        content={
            "title": "Memealerts",
            "message": "Привязка к аккаунту Memealerts удалена.",
        },
        status_code=200,
    )


@router.delete("/reward", response_class=JSONResponse)
@inject
async def disable_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
):
    if not user.memealerts.memealerts_reward:
        return JSONResponse(
            content={
                "title": "Ошибка",
                "message": "Награда не найдена.",
            },
            status_code=404,
        )
    await twitch.disable_reward(user, user.memealerts.memealerts_reward)
    return JSONResponse(
        content={
            "title": "Memealerts",
            "message": "Награда для начисления мемкоинов отключена.",
        },
        status_code=200,
    )


@router.put("/reward", response_class=JSONResponse, status_code=201)
@inject
async def create_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    db_session_factory: Annotated[Callable[[], AsyncSession], Depends(Provide[Container.db_session_factory])],
    user: User = Security(user_auth),
):
    try:
        reward = await twitch.create_reward(
            user,
            f"Получить {user.memealerts.memecoin_name_accusative_multiple.lower() or 'мемкоины'}",
            1000,
            "Награда начисляется автомагически. В комментарии к награде обязательно укажи свой полный ник или ID на Memealerts. ОБЯЗАТЕЛЬНО заберите приветственный бонус.",
            is_user_input_required=True,
        )
    except TwitchAPIException as exc:
        if "CREATE_CUSTOM_REWARD_DUPLICATE_REWARD" in str(exc):
            return JSONResponse({"title": "Ошибка", "message": "Награда уже существует."}, 400)
        if "CREATE_CUSTOM_REWARD_TOO_MANY_REWARDS" in str(exc):
            return JSONResponse(
                {"title": "Ошибка", "message": "Слишком много наград на канале."},
                400,
            )
        return JSONResponse(
            {"title": "Ошибка", "message": str(exc)},
            400,
        )

    async with db_session_factory() as db:
        await db.execute(
            sa.update(MemealertsSettings)
            .where(MemealertsSettings.user_id == user.id)
            .values(
                memealerts_reward=reward.id
            )
        )
        await db.commit()
    await twitch.subscribe_reward(user, reward.id)
    return JSONResponse({"title": "Успешно", "message": "Награда создана."}, 201)


@router.patch("/reward", response_class=JSONResponse)
@inject
async def update_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
):
    if not user.memealerts.memealerts_reward:
        return JSONResponse({"title": "Ошибка", "message": "Награда не существует."}, 400)
    reward = await twitch.update_reward(
        user,
        user.memealerts.memealerts_reward,
        is_enabled=True,
        is_user_input_required=True,
        should_redemptions_skip_request_queue=False,
    )
    return JSONResponse({"title": "Успешно", "message": "Награда обновлена."}, 200)