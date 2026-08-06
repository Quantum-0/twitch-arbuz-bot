import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Security, UploadFile
from httpx import HTTPStatusError
from jwt import DecodeError
from memealerts.types.exceptions import MATokenExpiredError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from starlette.responses import JSONResponse
from twitchAPI.type import TwitchAPIException, TwitchResourceNotFound

from container import Container
from database.models import CharacterInfo, User
from dependencies import get_db
from routers.api.user.checks import router as checks_router
from routers.api.user.memealerts import router as memealerts_router
from routers.api.user.stats import router as stats_router
from routers.api.user.streamers import router as streamers_router
from routers.security_helpers import user_auth
from schemas.api import (
    BoolResponseSchema,
    TTSExternalSpeechSchema,
    TTSPermissionsSchema,
    TTSSettingsUpdateSchema,
    UpdateMemealertsCoinsSchema,
    UpdateSettingsForm,
    UUIDResponseSchema,
)
from schemas.enums import FileStorageDir
from services.memes import MemealertsService
from services.moderation import ModerationService
from services.s3 import FileStorage
from services.sse_manager import SSEManager
from services.stickers import StickersService
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from utils.enums import SSEChannel
from utils.memes import token_expires_in_days
from utils.stickers_query import build_stickers_query, serialize_sticker_rows
from utils.tts import ensure_tts_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["User API"])

router.include_router(checks_router)
router.include_router(memealerts_router)
router.include_router(streamers_router)
router.include_router(stats_router)


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


@router.post("/slovotron/tip", status_code=204)
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


@router.post("/slovotron/restart", status_code=204)
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
                            "message": "Подписка на уведомления о рейдах уже существует.",
                        },
                        409,
                    )
                raise
        elif data.enable_shoutout_on_raid is False:
            await twitch.unsubscribe_raid(user=user)

    return JSONResponse({"title": "Сохранено", "message": "Настройки успешно обновлены."}, 200)


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
            "message": "Количество выдаваемых мемкоинов за награду обновлено.",
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
                {
                    "title": "Ошибка",
                    "message": "Ошибка авторизации пользователя через токен.\nПроверьте корректность скопированного токена.\nЕсли не помогает - попробуйте переавторизоваться на мемалёртсе.",
                },
                400,
            )

        if memealerts_user.channel:
            user.settings.memealerts_link = memealerts_user.channel.unique_link
            if memealerts_user.channel.currency_name_declensions:
                user.memealerts.memecoin_name_genitive = (
                    memealerts_user.channel.currency_name_declensions.genitive
                )  # 2 мемкоина
                user.memealerts.memecoin_name_accusative = (
                    memealerts_user.channel.currency_name_declensions.accusative
                )  # 1 мемкоин
                if memealerts_user.channel.currency_name_declensions.multiple:
                    user.memealerts.memecoin_name_genitive_multiple = (
                        memealerts_user.channel.currency_name_declensions.multiple.genitive
                    )  # 5 мемкоинов
                    user.memealerts.memecoin_name_accusative_multiple = (
                        memealerts_user.channel.currency_name_declensions.multiple.accusative
                    )  # Получить мемкоины

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
        if reward_id:
            problems = await twitch.validate_reward_subscription(user=user, reward_id=str(reward_id))
            if not problems:
                return JSONResponse({"title": "Без изменений", "message": "Уже включено."}, 208)
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
            return JSONResponse({"title": "Без изменений", "message": "Уже выключено."}, 208)

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
    mode: Literal["mine", "with_me", "from_me"] = Query(default="mine"),
    before: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=30),
):
    before_dt: datetime | None = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(400, "Неверный формат даты для параметра 'before'. Ожидается ISO формат.")
    q = build_stickers_query(mode, int(user.twitch_id), user.login_name, before=before_dt, limit=limit)
    rows = (await db.execute(q)).all()
    return serialize_sticker_rows(rows, limit)


@router.post("/reference")
@inject
async def upload_reference(
    db_session_factory: Annotated[Callable[[], AsyncSession], Depends(Provide[Container.db_session_factory])],
    s3: Annotated[FileStorage, Depends(Provide[Container.s3])],
    user: User = Security(user_auth),
    file: UploadFile | None = File(default=None),
    description: str | None = Form(default=None),
    name: str | None = None,
) -> BoolResponseSchema:
    has_file = bool(file and file.filename)
    if not has_file and not (description or "").strip():
        raise HTTPException(status_code=400, detail="Either file or description must be provided.")

    if name is not None and user.login_name != "quantum075":
        raise HTTPException(status_code=403, detail="You have no access to upload reference by custom name")

    file_bytes = b""
    if file is not None and file.filename:
        if file.size is not None and file.size > 10_000_000:
            raise HTTPException(
                status_code=413,
                detail="File is too large. Maximum size is 10 MB.",
            )
        if file.content_type != "image/png":
            raise HTTPException(status_code=415, detail="Invalid file type. Only PNG images are allowed.")

        file_bytes = await file.read()

        if len(file_bytes) < 8 or file_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            raise HTTPException(415, detail="Invalid file type. Only PNG images are allowed.")
        logger.info(f"Reference image from {user.login_name} was loaded to server")

    target_username = name or user.login_name.lower()
    new_image_id = uuid4() if has_file else None
    old_file_id_to_delete = None

    async with db_session_factory() as session:
        try:
            result = await session.execute(sa.select(CharacterInfo).where(CharacterInfo.name == target_username))
            existing_info = result.scalar_one_or_none()

            if existing_info:
                logger.info(f"Found already existing character info from {user.login_name}")
                session.add(existing_info)
                if has_file:
                    if existing_info.file_id:
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
            raise HTTPException(status_code=500, detail="Database error occurred.")
    logger.info(f"Character info from {user.login_name} saved to db")

    if has_file and new_image_id:
        try:
            await s3.put_object(f"{FileStorageDir.REFS}/{new_image_id}.png", file_bytes)
            logger.info(f"Reference image from {user.login_name} uploaded to s3 successfully")
        except Exception:
            logger.critical("Failed to upload S3 reference")
            raise HTTPException(status_code=500, detail="File saved to DB, but failed to upload to storage.")

    if old_file_id_to_delete:
        try:
            await s3.delete_object(f"{FileStorageDir.REFS}/{old_file_id_to_delete}.png")
            logger.info(f"Old reference image from {user.login_name} deleted from s3 successfully")
        except Exception as e:
            logger.error(f"Warning: Failed to delete old file {old_file_id_to_delete} from S3: {e}", exc_info=True)

    return BoolResponseSchema(result=True)


# ── TTS ──────────────────────────────────────────────────────────────────────


@router.post("/setup-tts")
@inject
async def setup_tts(
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: Any = Security(user_auth),
    enable: bool = Query(default=True),
):
    """Создать/удалить награду TTS за баллы канала."""
    tts = await ensure_tts_settings(db, user)
    reward_id = tts.tts_reward_id

    if enable:
        if reward_id:
            problems = await twitch.validate_reward_subscription(user=user, reward_id=str(reward_id))
            if not problems:
                return JSONResponse({"title": "Без изменений", "message": "Награда уже включена."}, 208)
            if "Награда не найдена" in problems:
                tts.tts_reward_id = None
                await db.commit()
                await db.refresh(tts)
                reward_id = None
            else:
                try:
                    await twitch.subscribe_reward(user, reward_id)
                except TwitchAPIException as exc:
                    return JSONResponse({"title": "Ошибка", "message": str(exc)}, 400)
                return JSONResponse({"title": "Успешно", "message": "Подписка на награду восстановлена."}, 200)
    else:
        if not reward_id:
            return JSONResponse({"title": "Без изменений", "message": "Награда уже выключена."}, 208)

    if enable:
        try:
            reward = await twitch.create_reward(
                user,
                "TTS — озвучить сообщение",
                500,
                "Введи текст, который будет озвучен на стриме через TTS.",
                is_user_input_required=True,
            )
        except TwitchAPIException as exc:
            if "CREATE_CUSTOM_REWARD_DUPLICATE_REWARD" in str(exc):
                return JSONResponse({"title": "Ошибка", "message": "Награда уже существует."}, 400)
            if "CREATE_CUSTOM_REWARD_TOO_MANY_REWARDS" in str(exc):
                return JSONResponse({"title": "Ошибка", "message": "Слишком много наград на канале."}, 400)
            return JSONResponse({"title": "Ошибка", "message": str(exc)}, 400)

        tts.tts_reward_id = reward.id
        await db.commit()
        await db.refresh(tts)
        await twitch.subscribe_reward(user, reward.id)
        return JSONResponse({"title": "Успешно", "message": "Награда TTS создана."}, 201)
    try:
        await twitch.delete_reward(user, reward_id)
    except TwitchResourceNotFound:
        pass
    tts.tts_reward_id = None
    await db.commit()
    await db.refresh(tts)
    return JSONResponse({"title": "Успешно", "message": "Награда TTS удалена."}, 200)


@router.post("/tts/settings")
async def update_tts_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    data: TTSSettingsUpdateSchema,
    user: Any = Security(user_auth),
):
    """Обновить скалярные настройки TTS (enabled, model, cooldowns, max_length, read_username)."""
    tts = await ensure_tts_settings(db, user)
    for field in data.model_fields_set:
        value = getattr(data, field)
        if value is not None:
            setattr(tts, field, value)
    await db.commit()
    return JSONResponse({"title": "Сохранено", "message": "Настройки TTS обновлены."}, 200)


@router.post("/tts/permissions")
async def update_tts_permissions(
    db: Annotated[AsyncSession, Depends(get_db)],
    data: TTSPermissionsSchema,
    user: Any = Security(user_auth),
):
    """Обновить матрицу разрешений TTS (roles × triggers). Награда доступна всем, если включена."""
    tts = await ensure_tts_settings(db, user)
    tts.permissions = data.model_dump()
    await db.commit()
    return JSONResponse({"title": "Сохранено", "message": "Матрица разрешений обновлена."}, 200)


@router.post("/tts/reset-key")
async def reset_tts_external_key(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Any = Security(user_auth),
):
    """Сгенерировать новый внешний ключ для TTS."""
    tts = await ensure_tts_settings(db, user)
    tts.external_key = uuid4().hex
    await db.commit()
    await db.refresh(tts)
    return JSONResponse({"title": "Готово", "message": "Новый ключ сгенерирован.", "key": tts.external_key}, 200)


@router.post("/tts/{key}")
@inject
async def tts_external_speech(
    db: Annotated[AsyncSession, Depends(get_db)],
    ssem: Annotated[SSEManager, Depends(Provide[Container.sse_manager])],
    moderation: Annotated[ModerationService, Depends(Provide[Container.moderation_service])],
    key: str,
    payload: TTSExternalSpeechSchema,
    moderate: bool = Query(default=True),
):
    """Озвучить текст через внешний ключ (без авторизации Twitch, без проверки ролей).

    Ключ = auth. Параметр ``moderate`` (default=True) включает серверную модерацию
    текста — при обнаружении запретки возвращаем 422. Если ``moderate=False``,
    текст отправляется в оверлей как есть (для доверенных интеграций).
    """
    stmt = sa.select(User).options(joinedload(User.tts)).where(User.tts.has(external_key=key))
    user = await db.scalar(stmt)
    if user is None or user.tts is None or not user.tts.enabled:
        raise HTTPException(status_code=403, detail="Invalid or disabled TTS key")

    if moderate:
        result = moderation.validate(payload.input)
        if result.is_banned:
            raise HTTPException(
                status_code=422,
                detail=f"Сообщение заблокировано модерацией (найдено: «{result.found_word}»)",
            )

    await ssem.broadcast(
        int(user.twitch_id),
        SSEChannel.TTS,
        json.dumps({"text": payload.input, "model": payload.model or user.tts.model}),
    )
    return JSONResponse({"title": "OK", "message": "Отправлено в оверлей."}, 200)
