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
    if data.enable_help is not None:
        user.settings.enable_help = data.enable_help
    if data.enable_random is not None:
        user.settings.enable_random = data.enable_random
    if data.enable_fruit is not None:
        user.settings.enable_fruit = data.enable_fruit

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
    memealerts_token: str = Form(None, alias="key"),
    db: AsyncSession = Depends(get_db),
    twitch: Twitch = Depends(get_twitch),
):
    reward_id = user.memealerts.memealerts_reward

    if enable == bool(reward_id):
        return JSONResponse({"title": "Без изменений","message": f"Уже {'включено' if enable else 'выключено'}."}, 208)

    if enable:
        try:
            await token_expires_in_days(memealerts_token)
        except (MATokenExpiredError, DecodeError):
            return JSONResponse({"title": "Невалидный токен",
                                 "message": f"Токен, который вы используете - не является валидным. Попробуйте скопировать токен заново."},
                                400)

        reward = await twitch.create_reward(user)
        user.memealerts.memealerts_reward = reward.id
        user.memealerts.memealerts_token = memealerts_token
        await db.commit()
        await db.refresh(user.memealerts)
        print(await twitch.subscribe_reward(user, reward.id))
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
