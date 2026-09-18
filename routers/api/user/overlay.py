"""API для оверлеев: сброс overlay_secret и регистрация временных команд."""

import json
import logging
from typing import Annotated

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Security
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from container import Container
from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth
from services.cache import Cache
from utils.overlay_secret import ensure_overlay_secret, reset_overlay_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/overlay", tags=["Overlay settings"])

# TTL временных команд в Redis (15 минут). Оверлей обновляет каждые 5 минут.
TEMP_COMMANDS_TTL_S = 15 * 60


class CustomCommandSchema(BaseModel):
    channel_id: int
    name: str = Field(max_length=64)
    aliases: str = Field(max_length=256)
    description: str = Field(max_length=256)


@router.post("/custom-commands")
@inject
async def register_custom_command(
    request: Request,
    payload: Annotated[CustomCommandSchema, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
):
    """Регистрация временной команды оверлея.

    Вызывается из OBS browser source (chat-listener.js) каждые 5 минут.
    Без user_auth — вместо неё проверяется overlay_secret через заголовок
    ``X-Overlay-Secret``. Команды передаются фронтом, сервер не знает
    заранее какие команды будут — он только хранит их в Redis с TTL.
    """
    secret_header = request.headers.get("X-Overlay-Secret")
    if not secret_header:
        raise HTTPException(401, "No overlay secret provided")

    user = (await db.execute(sa.select(User).where(User.twitch_id == str(payload.channel_id)))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    secret = await ensure_overlay_secret(db, user)
    if secret_header != str(secret):
        raise HTTPException(403, "Invalid overlay secret")

    # Rate-limit: не чаще 1 запроса в 30с на channel_id+name.
    if not await cache.check_rate_limit(f"custom_cmds:{payload.channel_id}:{payload.name}", limit=1, window_s=30):
        return JSONResponse({"status": "rate_limited"}, 200)

    key = f"overlay_cmds:{payload.channel_id}:{payload.name}"
    command = {
        "name": payload.name,
        "aliases": payload.aliases,
        "description": payload.description,
    }
    await cache.set_str(key, json.dumps(command, ensure_ascii=False), ttl=TEMP_COMMANDS_TTL_S)
    return JSONResponse({"status": "ok"}, 200)


@router.post("/reset-secret")
async def reset_overlay_secret_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User = Security(user_auth),
):
    """Сгенерировать новый случайный overlay_secret.

    После сброса все ранее созданные ссылки на оверлеи с chat_control
    перестанут регистрировать временные команды. Стримеру нужно обновить
    OBS browser source с новой ссылкой из панели управления.
    """
    new_secret = await reset_overlay_secret(db, user)
    return JSONResponse(
        {
            "title": "Готово",
            "message": "Ключ оверлеев сброшен. Обновите ссылки на оверлеи в OBS.",
            "overlay_secret": str(new_secret),
        },
        200,
    )
