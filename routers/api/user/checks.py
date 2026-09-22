import logging
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Security
from memealerts.types.exceptions import MATokenExpiredError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from container import Container
from database.models import TelegramSettings, User
from dependencies import get_db
from exceptions import MAInvalidTokenError, MANoToken, MATokenRefreshError, MAUnavailableError, MAValidationRespError
from routers.security_helpers import user_auth
from schemas.api import (
    BaseErrorSchema,
    CheckMemealertsRewardStatusResponseSchema,
    CheckStatusResponseSchema,
)
from schemas.memealerts import MAChannel
from services.memes_v2 import MemealertsOAuthService, MemealertsV2Service
from services.sse_manager import SSEManager
from twitch.client.twitch import Twitch
from utils.enums import SSEChannel
from utils.tts import get_tts_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/check", tags=["User checks"])


@router.get(
    "/sse",
    response_model=CheckStatusResponseSchema,
    responses={401: {"description": "Unauthorized", "model": BaseErrorSchema}},
)
@inject
async def check_user_sse_connected(
    ssem: Annotated[SSEManager, Depends(Provide[Container.sse_manager])],
    user: User = Security(user_auth),
    channel: SSEChannel | None = None,
) -> CheckStatusResponseSchema:
    result = await ssem.has_clients(int(user.twitch_id), channel)
    return CheckStatusResponseSchema(
        result=result, problems=["OBS не открыт или оверлей не установлен"] if not result else []
    )


@router.get("/heat-installed", response_model=CheckStatusResponseSchema)
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
        return CheckStatusResponseSchema(result=False, problems=["Установлено другое расширение"])
    return CheckStatusResponseSchema(result=True, problems=[])


@router.get("/memealerts-token", response_model=CheckStatusResponseSchema)
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
    except (MATokenExpiredError, MAInvalidTokenError):
        return CheckStatusResponseSchema(result=False, problems=["Токен невалидный, требуется переавторизация"])
    except MATokenRefreshError as exc:
        return CheckStatusResponseSchema(
            result=False, problems=[f"Ошибка Memealerts при обновлении токена: {exc.error}"]
        )
    except httpx.HTTPError:
        return CheckStatusResponseSchema(result=False, problems=["Ошибка подключения к Memealerts"])
    except Exception:
        return CheckStatusResponseSchema(result=False, problems=["Неизвестная ошибка получения токена"])
    try:
        ma_user = await memealerts_api.get_user_info(ma_token)
    except MAUnavailableError:
        return CheckStatusResponseSchema(result=False, problems=["Ошибка подключения к Memealerts"])
    except MAInvalidTokenError:
        return CheckStatusResponseSchema(
            result=False, problems=["Ошибка авторизации при получении данных о пользователе"]
        )
    except MAValidationRespError:
        return CheckStatusResponseSchema(result=False, problems=["Ошибка формирования ответа в Memealerts"])

    return CheckStatusResponseSchema(result=True, problems=[], warnings=_ma_channel_warnings(ma_user.channel))


def _ma_channel_warnings(channel: MAChannel | None) -> list[str]:
    """Варнинги по настройкам канала Memealerts (welcome-bonus / стикеры)."""
    if channel is None:
        return []
    warnings: list[str] = []
    if channel.welcome_bonus_enabled is False:
        warnings.append("Приветственный бонус выключен — зрители не смогут получить первые мемкоины за награду")
    if channel.disable_stickers is True:
        warnings.append("Отправка стикеров выключена на канале Memealerts")
    return warnings


@router.get("/memealerts-reward", response_model=CheckMemealertsRewardStatusResponseSchema)
@inject
async def check_memealerts_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
) -> CheckMemealertsRewardStatusResponseSchema:
    problems = await twitch.validate_reward_subscription(
        user=user,
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


@router.get("/ai-stickers-reward", response_model=CheckMemealertsRewardStatusResponseSchema)
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


@router.get("/tts-reward", response_model=CheckMemealertsRewardStatusResponseSchema)
@inject
async def check_tts_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
) -> CheckMemealertsRewardStatusResponseSchema:
    tts = get_tts_settings(user)
    if not tts.tts_reward_id:
        return CheckMemealertsRewardStatusResponseSchema(result=False, problems=["Награда не создана"], state="missing")
    problems = await twitch.validate_reward_subscription(user=user, reward_id=str(tts.tts_reward_id))
    if not problems:
        state = "ok"
    elif "Награда не найдена" in problems:
        state = "missing"
    else:
        state = "broken"
    return CheckMemealertsRewardStatusResponseSchema(result=not problems, problems=problems, state=state)


@router.get("/tts-overlay", response_model=CheckStatusResponseSchema)
@inject
async def check_tts_overlay(
    ssem: Annotated[SSEManager, Depends(Provide[Container.sse_manager])],
    user: User = Security(user_auth),
) -> CheckStatusResponseSchema:
    result = await ssem.has_clients(int(user.twitch_id), SSEChannel.TTS)
    return CheckStatusResponseSchema(result=result, problems=["TTS-оверлей не подключён к OBS"] if not result else [])


@router.get("/tts-server", response_model=CheckStatusResponseSchema)
async def check_tts_server() -> CheckStatusResponseSchema:
    """Проверить доступность внешнего TTS-сервера через /health."""
    parts = urlsplit(settings.tts_api_url)
    health_url = urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(health_url)
    except httpx.HTTPError:
        return CheckStatusResponseSchema(result=False, problems=["TTS-сервер недоступен"])
    if resp.status_code != 200:
        return CheckStatusResponseSchema(result=False, problems=[f"TTS-сервер вернул {resp.status_code}"])
    try:
        body = resp.json()
    except (ValueError, KeyError):
        return CheckStatusResponseSchema(result=False, problems=["TTS-сервер вернул некорректный ответ"])
    if body.get("status") != "ok":
        return CheckStatusResponseSchema(result=False, problems=["TTS-сервер сообщил об ошибке"])
    if not body.get("rvc_available"):
        return CheckStatusResponseSchema(result=False, problems=["TTS-сервер запущен, но RVC-сервер недоступен"])
    return CheckStatusResponseSchema(result=True, problems=[])


@router.get("/telegram", response_model=CheckStatusResponseSchema)
@inject
async def check_telegram(
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User = Security(user_auth),
) -> CheckStatusResponseSchema:
    """Проверить доступность TG-сервиса и статус подключения бота к чатам.

    1. Healthcheck TG-микросервиса (GET /healthcheck).
    2. Для каждого подключённого scope (stream/clips/stickers): запрос
       POST /api/chat_status → если бота нет в чате — сбросить привязку в БД
       и добавить проблему в список. Для stream также отписаться от EventSub.
    """
    tg = user.telegram

    # 1. Проверка доступности TG-сервиса.
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{settings.telegram_service_url}/api/healthcheck")
    except httpx.HTTPError:
        return CheckStatusResponseSchema(result=False, problems=["TG-сервис недоступен"])
    if resp.status_code != 200:
        return CheckStatusResponseSchema(result=False, problems=[f"TG-сервис вернул {resp.status_code}"])

    if tg is None or not (tg.stream_chat_id or tg.clips_chat_id or tg.stickers_chat_id):
        return CheckStatusResponseSchema(result=True, problems=[])

    # 2. Проверка каждого подключённого scope.
    scope_chats: list[tuple[Literal["stream", "clips", "stickers"], str | None, str]] = [
        ("stream", tg.stream_chat_id, "уведомления о стриме"),
        ("clips", tg.clips_chat_id, "клипы"),
        ("stickers", tg.stickers_chat_id, "AI-стикеры"),
    ]

    problems: list[str] = []
    stream_kicked = False
    for scope, chat_id, label in scope_chats:
        if not chat_id:
            continue
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                status_resp = await client.post(
                    f"{settings.telegram_service_url}/api/chat_status",
                    headers={"X-Api-Key": settings.telegram_service_api_key},
                    json={"chat_id": chat_id},
                )
        except httpx.HTTPError:
            problems.append(f"Не удалось проверить чат «{label}» (TG-сервис недоступен)")
            continue
        if status_resp.status_code != 200:
            problems.append(f"Не удалось проверить чат «{label}» (код {status_resp.status_code})")
            continue
        status_data = status_resp.json()
        if not status_data.get("member", False):
            # Бота кикнули — сбрасываем привязку прямо в текущей сессии.
            _reset_scope_binding(tg, scope)
            problems.append(f"Бот удалён из чата «{label}» — привязка очищена")
            if scope == "stream":
                stream_kicked = True
            continue
        if not status_data.get("can_post", False):
            problems.append(f"Бот в чате «{label}», но не может отправлять сообщения")

    if problems:
        await db.commit()
        if stream_kicked:
            try:
                await twitch.unsubscribe_stream_online(user)
                await twitch.unsubscribe_stream_offline(user)
            except Exception:
                logger.error("check_telegram: ошибка отписки EventSub для stream", exc_info=True)

    return CheckStatusResponseSchema(result=len(problems) == 0, problems=problems)


def _reset_scope_binding(tg: TelegramSettings, scope: Literal["stream", "clips", "stickers"]) -> None:
    """Обнулить привязку чата для scope прямо на ORM-объекте (в текущей сессии)."""
    if scope == "stream":
        tg.stream_chat_id = None
        tg.stream_chat_type = None
        tg.stream_chat_title = None
        tg.stream_connected_at = None
        tg.stream_notification_enabled = False
        tg.last_stream_message_id = None
        tg.twitch_to_tg_enabled = False
        tg.tg_to_twitch_enabled = False
    elif scope == "clips":
        tg.clips_chat_id = None
        tg.clips_chat_type = None
        tg.clips_chat_title = None
        tg.clips_connected_at = None
        tg.clips_enabled = False
    elif scope == "stickers":
        tg.stickers_chat_id = None
        tg.stickers_chat_type = None
        tg.stickers_chat_title = None
        tg.stickers_connected_at = None
        tg.stickers_enabled = False
