from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import httpx
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Security
from memealerts.types.exceptions import MATokenExpiredError

from config import settings
from container import Container
from database.models import User
from exceptions import MAInvalidTokenError, MANoToken, MATokenRefreshError, MAUnavailableError, MAValidationRespError
from routers.security_helpers import user_auth
from schemas.api import (
    BaseErrorSchema,
    CheckMemealertsRewardStatusResponseSchema,
    CheckStatusResponseSchema,
)
from schemas.memealerts import MAChannel
from services.memes_v2 import MemealertsOAuthService, MemealertsV2Service
from services.node_manager import NodeManager
from services.sse_manager import SSEManager
from twitch.client.twitch import Twitch
from utils.enums import SSEChannel
from utils.tts import get_tts_settings

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
@inject
async def check_tts_server(
    node_manager: Annotated[NodeManager, Depends(Provide[Container.node_manager])],
    user: User = Security(user_auth),
) -> CheckStatusResponseSchema:
    """Проверить доступность TTS-бэкенда.

    Для ``tts_backend == "api"`` — пинг внешнего сервера через /health.
    Для ``tts_backend == "nodes"`` — есть ли онлайн GPU-нода с моделью стримера.
    """
    if settings.tts_backend == "nodes":
        tts = get_tts_settings(user)
        model = tts.model or settings.tts_model
        if node_manager.is_available(model):
            return CheckStatusResponseSchema(result=True, problems=[])
        return CheckStatusResponseSchema(result=False, problems=[f"Нет онлайн-ноды с моделью '{model}'"])

    # api-бэкенд: пинг внешнего TTS-сервера.
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
