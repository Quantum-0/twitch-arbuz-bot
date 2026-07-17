import logging
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import uuid4

import httpx
import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, Query, Security, HTTPException, UploadFile, File
from httpx import HTTPStatusError
from jwt import DecodeError
from memealerts.types.exceptions import MATokenExpiredError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from twitchAPI.type import TwitchAPIException, TwitchResourceNotFound

from container import Container
from config import settings
from database.models import User, CharacterInfo, GeneratedImage
from dependencies import get_db
from exceptions import MANoToken, MAValidationRespError, MAUnavailableError, MAInvalidTokenError
from routers.security_helpers import user_auth
from schemas.api import (
    UpdateSettingsForm,
    UpdateMemealertsCoinsSchema,
    BoolResponseSchema,
    BaseErrorSchema,
    UUIDResponseSchema,
    CheckStatusResponseSchema,
    CheckMemealertsRewardStatusResponseSchema,
)
from schemas.enums import FileStorageDir
from services.memes import MemealertsService
from services.memes_v2 import MemealertsOAuthService, MemealertsV2Service
from services.s3 import FileStorage
from services.sse_manager import SSEManager
from services.stickers import StickersService
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from utils.enums import SSEChannel
from utils.memes import token_expires_in_days
from routers.api.user.memealerts import router as memealerts_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["User API"])

router.include_router(memealerts_router)


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



@router.post(
    "/slovotron/restart",
    status_code=204
)
@inject
async def slovotron_restart(
    db: Annotated[AsyncSession, Depends(get_db)],
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    channel: Annotated[str, Query(...)],
):
    q = sa.select(User).where(User.login_name == channel)
    res: User | None = await db.scalar(q)
    if not res:
        raise HTTPException(status_code=404, detail="User not found")
    await chat_bot.send_message(res, "!словотрон-рестарт")


# FIXME: все ручки /check-* перенести в отдельный роутер!
@router.get(
    "/check-sse",
    response_model=CheckStatusResponseSchema,
    responses={401: {"description": "Unauthorized", "model": BaseErrorSchema}},
)
@inject
async def check_user_sse_connected(
    ssem: Annotated[SSEManager, Depends(Provide[Container.sse_manager])],
    user: User = Security(user_auth),
    channel: SSEChannel | None = None,
) -> CheckStatusResponseSchema:
    result = ssem.has_clients(int(user.twitch_id), channel)
    return CheckStatusResponseSchema(result=result, problems=["OBS не открыт или оверлей не установлен"] if not result else [])


@router.get("/check-heat-installed", response_model=CheckStatusResponseSchema)
@inject
async def check_heat_installed(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
) -> CheckStatusResponseSchema:
    exts = await twitch.get_user_active_ext(user)
    overlay = exts.overlay.get("1")
    if not overlay:
        return CheckStatusResponseSchema(result=False, problems=["Расширение твича не установлено"])
    if not overlay.active:
        return CheckStatusResponseSchema(result=False, problems=["Расширение твича не активно"])
    if overlay.id != "cr20njfkgll4okyrhag7xxph270sqk":
        CheckStatusResponseSchema(result=False, problems=["Установлено другое расширение"])
    return CheckStatusResponseSchema(result=True, problems=[])


@router.get("/check-memealerts-token", response_model=CheckStatusResponseSchema)
@inject
async def check_memealerts_token(
    memealerts_auth: Annotated[MemealertsOAuthService, Depends(Provide[Container.memealerts_auth])],
    memealerts_api: Annotated[MemealertsV2Service, Depends(Provide[Container.memealerts_v2])],
    user: User = Security(user_auth),
) -> CheckStatusResponseSchema:
    try:
        ma_token = await memealerts_auth.get_token_of_user(user)
    except MANoToken:
        return CheckStatusResponseSchema(result=False, problems=["Токен OAuth отсутствует"])
    except MATokenExpiredError:
        return CheckStatusResponseSchema(result=False, problems=["Токен невалидный, требуется переавторизация"])
    except httpx.HTTPError:
        return CheckStatusResponseSchema(result=False, problems=["Ошибка подключения к Memealerts"])
    except Exception:
        return CheckStatusResponseSchema(result=False, problems=["Неизвестная ошибка получения токена"])
    try:
        await memealerts_api.get_user_info(ma_token)
    except MAUnavailableError:
        return CheckStatusResponseSchema(result=False, problems=["Ошибка подключения к Memealerts"])
    except MAInvalidTokenError:
        return CheckStatusResponseSchema(result=False, problems=["Ошибка авторизации при получении данных о пользователе"])
    except MAValidationRespError:
        return CheckStatusResponseSchema(result=False, problems=["Ошибка формирования ответа в Memealerts"])
    return CheckStatusResponseSchema(result=True, problems=[])


# FIXME: все ручки /check-* перенести в отдельный роутер!
@router.get("/check-memealerts-reward", response_model=CheckMemealertsRewardStatusResponseSchema)
@inject
async def check_memealerts_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
) -> CheckMemealertsRewardStatusResponseSchema:
    problems = await twitch.validate_reward_subscription(
        user = user,
        reward_id=str(user.memealerts.memealerts_reward),
    )
    if not problems:
        state = "ok"
    elif "Награда не найдена" in problems:
        state = "missing"
    else:
        state = "broken"
    return CheckMemealertsRewardStatusResponseSchema(
        result=not problems,
        problems=problems,
        state=state,
    )


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


@router.post("/memealerts", deprecated=True)
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
            logger.warning("Error authorization on MA", exc_info=True)
            return JSONResponse(
                {"title": "Ошибка", "message": "Ошибка авторизации пользователя через токен.\nПроверьте корректность скопированного токена.\nЕсли не помогает - попробуйте переавторизоваться на мемалёртсе."},
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




@router.get("/check-ai-stickers-reward", response_model=CheckMemealertsRewardStatusResponseSchema)
@inject
async def check_ai_stickers_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
) -> CheckMemealertsRewardStatusResponseSchema:
    if not user.settings.ai_sticker_reward_id:
        return CheckMemealertsRewardStatusResponseSchema(result=False, problems=["Награда не создана"], state="missing")
    problems = await twitch.validate_reward_subscription(user=user, reward_id=str(user.settings.ai_sticker_reward_id))
    if not problems:
        state = "ok"
    elif "Награда не найдена" in problems:
        state = "missing"
    else:
        state = "broken"
    return CheckMemealertsRewardStatusResponseSchema(result=not problems, problems=problems, state=state)

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
        if reward_id:
            problems = await twitch.validate_reward_subscription(user=user, reward_id=str(reward_id))
            if not problems:
                return JSONResponse(
                    {"title": "Без изменений", "message": "Уже включено."}, 208
                )
            if "Награда не найдена" in problems:
                user.settings.ai_sticker_reward_id = None
                await db.commit()
                await db.refresh(user.settings)
                reward_id = None
            else:
                try:
                    await twitch.subscribe_reward(user, reward_id)
                except TwitchAPIException as exc:
                    return JSONResponse({"title": "Ошибка", "message": str(exc)}, 400)
                return JSONResponse({"title": "Успешно", "message": "Подписка на награду восстановлена."}, 200)
    else:
        if not reward_id:
            return JSONResponse(
                {"title": "Без изменений", "message": "Уже выключено."}, 208
            )

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
        await db.refresh(user.settings)
        return JSONResponse({"title": "Успешно", "message": "Награда удалена."}, 200)




@router.get("/ai-stickers/recent")
async def get_recent_ai_stickers(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User = Security(user_auth),
    before: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=30),
):
    before_dt: datetime | None = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(400, "Неверный формат даты для параметра 'before'. Ожидается ISO формат.")
    q = (
        sa.select(GeneratedImage)
        .where(GeneratedImage.on_channel == int(user.twitch_id))
        .where(GeneratedImage.file_id.is_not(None))
        .where(
            GeneratedImage.created_at
            > sa.func.now() - sa.text(f"interval '{settings.s3_sticker_expires_days} days'")
        )
        .order_by(GeneratedImage.created_at.desc())
        .limit(limit + 1)
    )
    if before_dt:
        q = q.where(GeneratedImage.created_at < before_dt)
    rows = (await db.execute(q)).scalars().all()
    items = rows[:limit]
    next_cursor = items[-1].created_at.isoformat() if len(rows) > limit and items else None
    return {
        "items": [
            {
                "file_id": str(item.file_id),
                "prompt": item.prompt,
                "by_chatter": item.by_chatter,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ],
        "next_cursor": next_cursor,
    }

@router.post("/reference")
@inject
async def upload_reference(
    db_session_factory: Annotated[Callable[[], AsyncSession], Depends(Provide[Container.db_session_factory])],
    s3: Annotated[FileStorage, Depends(Provide[Container.s3])],
    user: User = Security(user_auth),
    file: UploadFile | None = File(default=None),
    description: str | None = Form(default=None),
    name: str | None = None
) -> BoolResponseSchema:
    if not file and not description:
        raise HTTPException(
            status_code=400,
            detail="Either file or description must be provided."
        )

    if name is not None and user.login_name != "quantum075":
        raise HTTPException(
            status_code=403,
            detail="You have no access to upload reference by custom name"
        )

    if file:
        if file.size > 10_000_000:
            raise HTTPException(
                status_code=413,
                detail="File is too large",
            )
        if file.content_type != "image/png":
            raise HTTPException(
                status_code=415,
                detail="Invalid file type. Only PNG images are allowed."
            )

        file_bytes = await file.read()

        if file_bytes[:8] != b'\x89PNG\r\n\x1a\n':
            raise HTTPException(415, detail="Invalid file type. Only PNG images are allowed.")
        logger.info(f"Reference image from {user.login_name} was loaded to server")

    target_username = name or user.login_name.lower()
    new_image_id = uuid4() if file else None
    old_file_id_to_delete = None

    async with db_session_factory() as session:
        try:
            result = await session.execute(
                sa.select(CharacterInfo).where(CharacterInfo.name == target_username)
            )
            existing_info = result.scalar_one_or_none()

            if existing_info:
                logger.info(f"Found already existing character info from {user.login_name}")
                session.add(existing_info)
                if file and existing_info.file_id:
                    old_file_id_to_delete = existing_info.file_id
                    existing_info.file_id = new_image_id
                if description:
                    existing_info.description = description
            else:
                new_info = CharacterInfo(
                    name=target_username,
                    description=description,
                    file_id=new_image_id,
                )
                session.add(new_info)
            await session.commit()
        except Exception:
            await session.rollback()
            raise HTTPException(
                status_code=500,
                detail="Database error occurred."
            )
    logger.info(f"Character info from {user.login_name} saved to db")

    if file and new_image_id:
        try:
            await s3.put_object(f"{FileStorageDir.REFS}/{new_image_id}.png", file_bytes)
            logger.info(f"Reference image from {user.login_name} uploaded to s3 successfully")
        except Exception as e:
            logger.critical("Failed to upload S3 reference")
            raise HTTPException(
                status_code=500,
                detail="File saved to DB, but failed to upload to storage."
            )

    if old_file_id_to_delete:
        try:
            await s3.delete_object(f"{FileStorageDir.REFS}/{old_file_id_to_delete}.png")
            logger.info(f"Old reference image from {user.login_name} deleted from s3 successfully")
        except Exception as e:
            logger.error(f"Warning: Failed to delete old file {old_file_id_to_delete} from S3: {e}", exc_info=True)

    return BoolResponseSchema(result=True)
