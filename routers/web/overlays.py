import json
import logging
from typing import Annotated, Literal

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.params import Depends
from pydantic import BaseModel
from pydantic.color import Color
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.templating import Jinja2Templates

from config import settings
from container import Container
from database.models import User
from dependencies import get_db
from services.cache import Cache
from services.tts import TTSService, TTSServiceError
from utils.overlay_secret import compute_legacy_overlay_secret, ensure_overlay_secret
from utils.overlays import touch_overlay_usage
from utils.template_globals import register_template_globals
from utils.tts import get_tts_settings

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")
register_template_globals(templates)

router = APIRouter(prefix="/overlay", tags=["OBS overlays and widgets"])


@router.get("/jumping-chibi")
async def overlay_jumping_chibi(
    request: Request,
    timer: Annotated[int, Query()] = 3 * 60,
):
    return templates.TemplateResponse(
        "overlays/jumping-chibi.html",
        {
            "request": request,
            "timer": timer * 1000,
        },
    )


@router.get("/tts")
async def overlay_tts(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    channel_id: Annotated[int, Query()],
):
    await touch_overlay_usage(channel_id=channel_id)
    user = (
        await db.execute(sa.select(User).options(joinedload(User.tts)).where(User.twitch_id == str(channel_id)))
    ).scalar_one_or_none()
    tts = get_tts_settings(user) if user else None
    return templates.TemplateResponse(
        "overlays/tts.html",
        {
            "request": request,
            "channel_id": channel_id,
            "model": (tts.model if tts and tts.model else settings.tts.model),
            "tts_speech_url": f"{request.base_url}overlay/tts/speech",
        },
    )


class TTSSpeechSchema(BaseModel):
    input: str
    model: str | None = None
    response_format: str = "mp3"


@router.post("/tts/speech")
@inject
async def overlay_tts_speech(
    tts_service: Annotated[TTSService, Depends(Provide[Container.tts_service])],
    payload: Annotated[TTSSpeechSchema, Body()],
):
    text = payload.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty input")
    try:
        audio, content_type = await tts_service.synthesize(text, model=payload.model)
    except TTSServiceError as exc:
        # Отдаём ответ без выброса исключения, иначе sentry-starlette захватывает HTTPException 5xx как error.
        status_code = 503 if "not configured" in str(exc) else 502
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})
    return Response(content=audio, media_type=content_type)


@router.get("/slovotron")
async def overlay_slovotron(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    channel_name: Annotated[str, Query()],
    inactive_timeout: Annotated[int, Query()] = 20,
    inactive_opacity: Annotated[float, Query()] = 0.4,
):
    await touch_overlay_usage(channel_name=channel_name)
    user = (await db.execute(sa.select(User).where(User.login_name == channel_name))).scalar_one_or_none()
    if user:
        secret = await ensure_overlay_secret(db, user)
    else:
        secret = compute_legacy_overlay_secret(channel_name)
    return RedirectResponse(
        url=f"https://slovotron.fra3a.ru/?obs-overlay=1&"
        f"channel={channel_name}&inactive_timeout={inactive_timeout}&"
        f"inactive_opacity={inactive_opacity}&webhook_secret={secret}&"
        f"win_notification_enable=0&"
        f"webhook_url={request.base_url}api/webhook/slovotron"
    )


_DEFAULT_STAR_COLOR = Color("#ffd45a")


@router.get("/star")
async def overlay_star(
    request: Request,
    channel_id: Annotated[int, Query()],
    pos: Annotated[float, Query()] = 0.5,
    size: Annotated[int, Query()] = 16,
    color: Annotated[Color, Query()] = _DEFAULT_STAR_COLOR,
    length: Annotated[float, Query()] = 0.39,
    break_chance: Annotated[float, Query()] = 0.015,
    chat_control: Annotated[bool, Query()] = False,
    channel_name: Annotated[str, Query()] = "",
    # 0.05 == 100+1385 interactions to 50% chance to break
    # 0.03 == 100+2310 to 50%
    # 0.015 == 100+4620
    # 1.0 = 100+68 to 50%
):
    await touch_overlay_usage(channel_id=channel_id)
    return templates.TemplateResponse(
        "overlays/star.html",
        {
            "request": request,
            "channel_id": channel_id,
            "position": pos,
            "size": size,
            "color": color,
            "length": length,
            "break_chance": break_chance,
            "chat_control": chat_control,
            "channel_name": channel_name,
        },
    )


@router.get("/xp-error")
async def overlay_xp_error(
    request: Request,
    channel_id: Annotated[int, Query()],
    cooldown_min: Annotated[float, Query(ge=0)] = 5,
    scale: Annotated[float, Query(ge=0.3)] = 1.5,
    max_windows: Annotated[int, Query(ge=1)] = 5,
    sound: Annotated[bool, Query()] = False,
    debug: Annotated[bool, Query()] = False,
    channel_name: Annotated[str, Query()] = "",
):
    await touch_overlay_usage(channel_id=channel_id)
    return templates.TemplateResponse(
        "overlays/xp-error.html",
        {
            "request": request,
            "channel_id": channel_id,
            "cooldown_min": cooldown_min,
            "scale": scale,
            "max_windows": max_windows,
            "sound": sound,
            "debug": debug,
            "channel_name": channel_name,
        },
    )


@router.get("/fireworks")
async def overlay_fireworks(
    request: Request,
    channel_id: Annotated[int, Query()],
    particle_count: Annotated[int, Query(ge=10, le=300)] = 80,
    decay: Annotated[float, Query(ge=0.005, le=0.05)] = 0.015,
    gravity: Annotated[float, Query(ge=0.0, le=0.3)] = 0.08,
    chat_control: Annotated[bool, Query()] = False,
    channel_name: Annotated[str, Query()] = "",
):
    await touch_overlay_usage(channel_id=channel_id)
    return templates.TemplateResponse(
        "overlays/fireworks.html",
        {
            "request": request,
            "channel_id": channel_id,
            "particle_count": particle_count,
            "decay": decay,
            "gravity": gravity,
            "chat_control": chat_control,
            "channel_name": channel_name,
        },
    )


@router.get("/start-wait")
async def overlay_start_wait(
    request: Request,
    channel_id: Annotated[int, Query()],
):
    await touch_overlay_usage(channel_id=channel_id)
    return templates.TemplateResponse(
        "overlays/start_wait.html",
        {
            "request": request,
            "channel_id": channel_id,
        },
    )


@router.get("/ai-sticker")
async def overlay_ai_sticker(
    request: Request,
    channel_id: Annotated[int, Query()],
    sticker_lifetime: Annotated[int, Query()] = 30,
    sticker_size: Annotated[int, Query()] = 192,
):
    await touch_overlay_usage(channel_id=channel_id)
    return templates.TemplateResponse(
        "overlays/imggen.html",
        {
            "request": request,
            "channel_id": channel_id,
            "sticker_lifetime": sticker_lifetime,
            "sticker_size": sticker_size,
        },
    )


@router.get("/ya-music-widget")
async def overlay_ya_music_widget(
    request: Request,
    channel_id: Annotated[int, Query()],
    widget_type: Annotated[Literal["pulsma", "battlebeats"], Query(alias="widget-type")] = "pulsma",
):
    await touch_overlay_usage(channel_id=channel_id)
    return templates.TemplateResponse(
        f"overlays/ya-music-widget-{widget_type}.html",
        {
            "request": request,
            "channel_id": channel_id,
        },
    )


@router.get("/pair")
async def overlay_pair(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    channel_id: Annotated[int, Query()],
    use_twitch_emoji: Annotated[bool, Query()] = False,
    arbuz: Annotated[bool, Query()] = False,
    offset_left: Annotated[float, Query()] = 0,
    offset_right: Annotated[float, Query()] = 0,
    offset_top: Annotated[float, Query()] = 0,
    offset_bottom: Annotated[float, Query()] = 0,
    card_scale: Annotated[float, Query()] = 0.7,
):
    await touch_overlay_usage(channel_id=channel_id)
    if not use_twitch_emoji and not arbuz:
        result = await db.execute(
            sa.union_all(
                sa.select(User.login_name.label("name"), User.profile_image_url.label("img"))
                .where(User.twitch_id != str(channel_id))
                .where(User.followers_count > 50)
                .order_by(sa.func.random())
                .limit(9),
                sa.select(User.login_name.label("name"), User.profile_image_url.label("img")).where(
                    User.twitch_id == str(channel_id)
                ),
            )
        )
        cards = [{"id": row.name, "img": row.img, "caption": row.name} for row in result.fetchall()]
    else:
        if arbuz:
            items: list[tuple[str, str]] = [
                ("Вкусьни", "/static/images/stickers/1.webp"),
                ("Цвяточк", "/static/images/stickers/2.webp"),
                ("Жёпь", "/static/images/stickers/3.webp"),
                ("Играц", "/static/images/stickers/4.webp"),
                ("Лапк", "/static/images/stickers/5.webp"),
                ("Смотритб", "/static/images/stickers/6.webp"),
                ("Думаетб", "/static/images/stickers/7.webp"),
                ("Кексик", "/static/images/stickers/8.png"),
                ("Питса", "/static/images/stickers/9.webp"),
                ("Хвостб", "/static/images/stickers/10.webp"),
            ]
        else:
            items = [
                ("CorgiDerp", "https://static-cdn.jtvnw.net/emoticons/v2/49106/default/dark/4.0"),
                ("Kappa", "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/4.0"),
                ("KomodoHype", "https://static-cdn.jtvnw.net/emoticons/v2/81273/default/dark/4.0"),
                ("KonCha", "https://static-cdn.jtvnw.net/emoticons/v2/160400/default/dark/4.0"),
                ("LUL", "https://static-cdn.jtvnw.net/emoticons/v2/425618/default/dark/4.0"),
                ("NotLikeThis", "https://static-cdn.jtvnw.net/emoticons/v2/58765/default/dark/4.0"),
                (
                    "TwitchConHYPE",
                    "https://static-cdn.jtvnw.net/emoticons/v2/emotesv2_13b6dd7f3a3146ef8dc10f66d8b42a96/default/dark/4.0",
                ),
                ("SeemsGood", "https://static-cdn.jtvnw.net/emoticons/v2/64138/default/dark/4.0"),
                (
                    "PewPewPew",
                    "https://static-cdn.jtvnw.net/emoticons/v2/emotesv2_587405136a8147148c77df74baaa1bf4/default/dark/4.0",
                ),
                ("OSFrog", "https://static-cdn.jtvnw.net/emoticons/v2/81248/default/dark/4.0"),
            ]
        cards = [{"id": item[0], "img": item[1], "caption": item[0]} for item in items]
    return templates.TemplateResponse(
        "overlays/pair.html",
        {
            "cards": cards,
            "offset": {
                "top": offset_top,
                "left": offset_left,
                "bottom": offset_bottom,
                "right": offset_right,
            },
            "card_scale": card_scale,
            "request": request,
            "channel_id": channel_id,
        },
    )


async def get_temp_commands(cache: Cache, twitch_id: str) -> list[dict[str, str]]:
    """Прочитать временные команды оверлеев из Redis (для /cmdlist).

    Сканирует все ключи ``overlay_cmds:{twitch_id}:*`` — сервер не знает
    заранее какие команды зарегистрированы, так как они определяются фронтом.
    """
    result: list[dict[str, str]] = []
    pattern = f"overlay_cmds:{twitch_id}:*"
    try:
        raw_redis = cache._r  # noqa: SLF001
        if raw_redis is None:
            return result
        async for key in raw_redis.scan_iter(match=f"cache:{pattern}", count=100):
            raw = await raw_redis.get(key)
            if raw:
                try:
                    result.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    pass
    except Exception:
        logger.warning("Error reading temp commands from Redis", exc_info=True)
    return result


@router.get("/halloween")
async def overlay_halloween(
    request: Request,
    channel_id: Annotated[int, Query()],
    channel_name: Annotated[str, Query()] = "",
):
    await touch_overlay_usage(channel_id=channel_id)
    return templates.TemplateResponse(
        "overlays/halloween.html",
        {
            "request": request,
            "channel_id": channel_id,
            "channel_name": channel_name,
        },
    )
