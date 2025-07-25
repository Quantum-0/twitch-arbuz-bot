from typing import Any, Annotated

from fastapi import APIRouter, Depends, Security, Form, Query
from jwt import DecodeError
from memealerts.types.exceptions import MATokenExpiredError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from twitchAPI.types import TwitchResourceNotFound

from dependencies import get_db, get_chat_bot, get_twitch
from routers.schemas import UpdateSettingsForm, UpdateMemealertsCoinsSchema
from routers.security_helpers import user_auth
from twitch.bot import ChatBot
from twitch.twitch import Twitch
from utils.memes import token_expires_in_days

router = APIRouter(prefix="/user", tags=["User API"])

@router.post("/update_settings")
async def update_settings(
    data: Annotated[UpdateSettingsForm, Form()],
    user: Any = Security(user_auth),
    db: AsyncSession = Depends(get_db),
    chat_bot: ChatBot = Depends(get_chat_bot),
):
    # user.settings: TwitchUserSettings
    # if data.enable_bite is not None:
    #     user.settings.enable_bite = data.enable_bite
    # if data.enable_lick is not None:
    #     user.settings.enable_lick = data.enable_lick
    # if data.enable_boop is not None:
    #     user.settings.enable_boop = data.enable_boop

    for field in data.model_fields_set:
        value = getattr(data, field)
        if value is not None:
            setattr(user.settings, field, value)

    await db.commit()
    await db.refresh(user.settings)

    await chat_bot.update_bot_channels()
    return JSONResponse({"title": "Сохранено", "message": f"Настройки успешно обновлены."}, 200)


@router.post("/memealerts/coins")
async def update_memealert_coins(
    data: UpdateMemealertsCoinsSchema,
    user: Any = Security(user_auth),
    db: AsyncSession = Depends(get_db),
):
    user.memealerts.coins_for_reward = data.count
    await db.commit()
    return JSONResponse({"title": "Сохранено", "message": f"Количество выдаваемых мемкоинов за награду обновлено."}, 200)


@router.post("/memealerts")
async def setup_memealert(
    user: Any = Security(user_auth),
    enable: bool = Query(...),
    memealerts_token: str | None = Form(None, alias="key"),
    db: AsyncSession = Depends(get_db),
    twitch: Twitch = Depends(get_twitch),
):
    reward_id = user.memealerts.memealerts_reward

    if enable == bool(reward_id):
        return JSONResponse({"title": "Без изменений","message": f"Уже {'включено' if enable else 'выключено'}."}, 208)

    if enable:
        if not memealerts_token:
            return JSONResponse({"title": "Ошибка","message": f"Ключ не передан."}, 422)
        memealerts_token = memealerts_token.strip().replace("Bearer", "").strip()
        try:
            await token_expires_in_days(memealerts_token)
        except (MATokenExpiredError, DecodeError):
            return JSONResponse({"title": "Невалидный токен",
                                 "message": f"Токен, который вы используете - не является валидным. Попробуйте скопировать токен заново."},
                                400)

        reward = await twitch.create_reward(
            user,
            "Memecoins",
            500,
            "Награда начисляется автомагически. В комментарии к награде обязательно укажи свой полный ник или ID на мемалёрте. По нику выдача ТОЛЬКО после получения приветственного бонуса.",
            is_user_input_required=True,
        )
        user.memealerts.memealerts_reward = reward.id
        user.memealerts.memealerts_token = memealerts_token
        await db.commit()
        await db.refresh(user.memealerts)
        await twitch.subscribe_reward(user, reward.id)
        return JSONResponse({"title": "Успешно", "message": f"Награда создана."}, 201)
    else:
        try:
            await twitch.delete_reward(user, reward_id)
        except TwitchResourceNotFound:
            pass
        user.memealerts.memealerts_reward = None
        await db.commit()
        await db.refresh(user.memealerts)
        return JSONResponse({"title": "Успешно", "message": f"Награда удалена."}, 200)
