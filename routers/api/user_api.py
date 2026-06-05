from typing import Annotated, Any

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, Query, Security, HTTPException
from httpx import HTTPStatusError
from jwt import DecodeError
from memealerts.types.exceptions import MATokenExpiredError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from twitchAPI.type import TwitchAPIException, TwitchResourceNotFound

from database.models import User
from container import Container
from dependencies import get_db
from schemas.api import (
    UpdateSettingsForm,
    UpdateMemealertsCoinsSchema,
    BoolResponseSchema,
    BaseErrorSchema,
    UUIDResponseSchema,
)
from routers.security_helpers import user_auth
from services.memes import MemealertsService
from services.sse_manager import SSEManager
from services.stickers import StickersService
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from utils.enums import SSEChannel
from utils.memes import token_expires_in_days

import sqlalchemy as sa

router = APIRouter(prefix="/user", tags=["User API"])


@router.get(
    "/not-shown-sticker-id",
)
@inject
async def get_not_shown_sticker_id(
    channel: int,
    stickers: Annotated[StickersService, Depends(Provide[Container.stickers_service])],
) -> UUIDResponseSchema:
    if unshown := await stickers.get_unshown(channel):
        return UUIDResponseSchema(id=unshown)
    raise HTTPException(404, "No stickers are not shown")


@router.post(
    "/slovotron/tip",
    status_code=204
)
@inject
async def slovotron_tip(
    db: Annotated[AsyncSession, Depends(get_db)],
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    channel: Annotated[str, Query(...)],
):
    q = sa.select(User).where(User.login_name == channel)
    res: User | None = await db.scalar(q)
    if not res:
        raise HTTPException(status_code=404, detail="User not found")
    await chat_bot.send_message(res, "!подсказка")



@router.get(
    "/check-sse",
    response_model=BoolResponseSchema,
    responses={401: {"description": "Unauthorized", "model": BaseErrorSchema}},
)
@inject
async def check_user_sse_connected(
    ssem: Annotated[SSEManager, Depends(Provide[Container.sse_manager])],
    user: User = Security(user_auth),
    channel: SSEChannel | None = None,
) -> BoolResponseSchema:
    return BoolResponseSchema(result=ssem.has_clients(int(user.twitch_id), channel))


@router.get("/check-heat-installed", response_model=BoolResponseSchema)
@inject
async def check_heat_installed(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
) -> BoolResponseSchema:
    exts = await twitch.get_user_active_ext(user)
    overlay = exts.overlay.get("1")
    if overlay and overlay.active and overlay.id == "cr20njfkgll4okyrhag7xxph270sqk":
        return BoolResponseSchema(result=True)
    return BoolResponseSchema(result=False)


@router.get("/install-heat", response_model=BoolResponseSchema)
@inject
async def install_heat(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
) -> BoolResponseSchema:
    await twitch.install_heat_ext(user)
    return BoolResponseSchema(result=True)


@router.post("/update_settings")
@inject
async def update_settings(
    data: Annotated[UpdateSettingsForm, Form()],
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
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

    return JSONResponse({"title": "Сохранено", "message": f"Настройки успешно обновлены."}, 200)


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
@inject
async def setup_memealert(
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    memealerts: Annotated[MemealertsService, Depends(Provide[Container.memealerts])],
    user: User = Security(user_auth),
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

        # TODO: валидируем токен, получаем ссылку, достаём из мемалёртса названия коинов
        #  записываем их в бд чтоб при выдаче награды говорить что начислины не коины а {название}
        #  включаем мемы и приветственный бонус, если они выключены (обязательно подписать это в окне в панели управления)
        #  ссылку сохраняем в пользователя, чтоб можно было потом ему ссылку указать в профиле
        #  а как обновлять старые? можно при получении коинов делать в фоне о.о
        try:
            memealerts_user = await memealerts.get_current(memealerts_token)
        except:
            return JSONResponse(
                {"title": "Ошибка", "message": "Ошибка авторизации пользователя через токен.\nПроверьте корректность скопированного токена."},
                400,
            )

        if memealerts_user.channel:
            user.settings.memealerts_link = memealerts_user.channel.unique_link
            if memealerts_user.channel.currency_name_declensions:
                user.memealerts.memecoin_name_genitive = memealerts_user.channel.currency_name_declensions.genitive # 2 мемкоина
                user.memealerts.memecoin_name_accusative = memealerts_user.channel.currency_name_declensions.accusative # 1 мемкоин
                if memealerts_user.channel.currency_name_declensions.multiple:
                    user.memealerts.memecoin_name_genitive_multiple = memealerts_user.channel.currency_name_declensions.multiple.genitive # 5 мемкоинов
                    user.memealerts.memecoin_name_accusative_multiple = memealerts_user.channel.currency_name_declensions.multiple.accusative # Получить мемкоины

            if memealerts_user.channel.disable_stickers is True:
                try:
                    await memealerts.enable_stickers(memealerts_token)
                except:
                    pass
            if memealerts_user.channel.welcome_bonus_enabled is False:
                try:
                    await memealerts.enable_welcome_bonus(memealerts_token)
                except:
                    pass

        if refresh:
            user.memealerts.memealerts_token = memealerts_token
            await db.commit()
            await db.refresh(user.memealerts)
            return JSONResponse({"title": "Успешно", "message": "Токен обновлён."}, 200)

        try:
            reward = await twitch.create_reward(
                user,
                f"Получить {user.memealerts.memecoin_name_accusative_multiple or 'Мемкоины'}",
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
@inject
async def setup_ai_stickers(
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
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
