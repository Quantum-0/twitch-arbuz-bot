from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Security
from httpx import HTTPStatusError
from jwt import DecodeError
from memealerts.types.exceptions import MATokenExpiredError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from twitchAPI.type import TwitchAPIException, TwitchResourceNotFound

from dependencies import get_chat_bot, get_db, get_twitch
from routers.schemas import UpdateMemealertsCoinsSchema, UpdateSettingsForm
from routers.security_helpers import user_auth
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from utils.memes import token_expires_in_days

router = APIRouter(prefix="/user", tags=["User API"])


@router.post("/update_settings")
async def update_settings(
    data: Annotated[UpdateSettingsForm, Form()],
    chat_bot: Annotated[ChatBot, Depends(get_chat_bot)],
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(get_twitch)],
    user: Any = Security(user_auth),
):
    for field in data.model_fields_set:
        value = getattr(data, field)
        if value is not None:
            setattr(user.settings, field, value)

    await db.commit()
    await db.refresh(user.settings)

    await chat_bot.update_bot_channels()

    if data.enable_shoutout_on_raid is not None:
        if data.enable_shoutout_on_raid is True:
            try:
                await twitch.subscribe_raid(user)
            except HTTPStatusError as exc:
                if exc.response.status_code == 409:
                    return JSONResponse(
                        {
                            "title": "Ошибка",
                            "message": f"Подписка на уведомления о рейдах уже существует.",
                        },
                        409,
                    )
                else:
                    raise
        elif data.enable_shoutout_on_raid is False:
            await twitch.unsubscribe_raid(user=user)

    return JSONResponse(
        {"title": "Сохранено", "message": f"Настройки успешно обновлены."}, 200
    )


@router.post("/memealerts/coins")
async def update_memealert_coins(
    db: Annotated[AsyncSession, Depends(get_db)],
    data: UpdateMemealertsCoinsSchema,
    user: Any = Security(user_auth),
):
    user.memealerts.coins_for_reward = data.count
    await db.commit()
    return JSONResponse(
        {
            "title": "Сохранено",
            "message": f"Количество выдаваемых мемкоинов за награду обновлено.",
        },
        200,
    )


@router.post("/memealerts")
async def setup_memealert(
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(get_twitch)],
    user: Any = Security(user_auth),
    enable: bool = Query(...),
    memealerts_token: str | None = Form(None, alias="key"),
    refresh: bool = Query(False),
):
    reward_id = user.memealerts.memealerts_reward

    if not refresh and enable == bool(reward_id):
        return JSONResponse(
            {
                "title": "Без изменений",
                "message": f"Уже {'включено' if enable else 'выключено'}.",
            },
            208,
        )

    if enable:
        if not memealerts_token:
            return JSONResponse({"title": "Ошибка", "message": "Ключ не передан."}, 422)
        memealerts_token = memealerts_token.strip().replace("Bearer", "").strip()
        try:
            await token_expires_in_days(memealerts_token)
        except (MATokenExpiredError, DecodeError):
            return JSONResponse(
                {
                    "title": "Невалидный токен",
                    "message": "Токен, который вы используете - не является валидным. Попробуйте скопировать токен заново.",
                },
                400,
            )

        if refresh:
            user.memealerts.memealerts_token = memealerts_token
            await db.commit()
            await db.refresh(user.memealerts)
            return JSONResponse({"title": "Успешно", "message": "Токен обновлён."}, 200)

        try:
            reward = await twitch.create_reward(
                user,
                "Memecoins",
                500,
                "Награда начисляется автомагически. В комментарии к награде обязательно укажи свой полный ник или ID на мемалёрте. По нику выдача ТОЛЬКО после получения приветственного бонуса.",
                is_user_input_required=True,
            )
        except TwitchAPIException as exc:
            if "CREATE_CUSTOM_REWARD_DUPLICATE_REWARD" in str(exc):
                return JSONResponse(
                    {"title": "Ошибка", "message": "Награда уже существует."}, 400
                )
            if "CREATE_CUSTOM_REWARD_TOO_MANY_REWARDS" in str(exc):
                return JSONResponse(
                    {"title": "Ошибка", "message": "Слишком много наград на канале."},
                    400,
                )

        user.memealerts.memealerts_reward = reward.id
        user.memealerts.memealerts_token = memealerts_token
        await db.commit()
        await db.refresh(user.memealerts)
        await twitch.subscribe_reward(user, reward.id)
        return JSONResponse({"title": "Успешно", "message": "Награда создана."}, 201)
    else:
        try:
            await twitch.delete_reward(user, reward_id)
        except TwitchResourceNotFound:
            pass
        user.memealerts.memealerts_reward = None
        await db.commit()
        await db.refresh(user.memealerts)
        return JSONResponse({"title": "Успешно", "message": "Награда удалена."}, 200)


@router.post("/setup-ai-stickers")
async def setup_ai_stickers(
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(get_twitch)],
    user: Any = Security(user_auth),
    enable: bool = Query(default=True),
):
    reward_id = user.settings.ai_sticker_reward_id

    if enable:
        try:
            reward = await twitch.create_reward(
                user,
                "AI Sticker",
                5000,
                "Введи описание, по которому будет сгенерирован стикер и налеплен стримеру на экран :з",
                is_user_input_required=True,
            )
        except TwitchAPIException as exc:
            if "CREATE_CUSTOM_REWARD_DUPLICATE_REWARD" in str(exc):
                return JSONResponse(
                    {"title": "Ошибка", "message": "Награда уже существует."}, 400
                )
            if "CREATE_CUSTOM_REWARD_TOO_MANY_REWARDS" in str(exc):
                return JSONResponse(
                    {"title": "Ошибка", "message": "Слишком много наград на канале."},
                    400,
                )

        user.settings.ai_sticker_reward_id = reward.id
        await db.commit()
        await db.refresh(user.settings)
        await twitch.subscribe_reward(user, reward.id)
        return JSONResponse({"title": "Успешно", "message": "Награда создана."}, 201)
    else:
        try:
            await twitch.delete_reward(user, reward_id)
        except TwitchResourceNotFound:
            pass
        user.settings.ai_sticker_reward_id = None
        await db.commit()
        await db.refresh(user.memealerts)
        return JSONResponse({"title": "Успешно", "message": "Награда удалена."}, 200)
