import logging
from typing import Annotated, Literal
from uuid import uuid3

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.params import Depends
from pydantic import BaseModel
from pydantic.color import Color
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.templating import Jinja2Templates

from config import settings
from database.models import User
from dependencies import get_db
from utils.overlays import touch_overlay_usage

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/overlay", tags=["OBS overlays and widgets"])


@router.get("/jumping-chibi")
async def overlay_jumping_chibi(
    request: Request,
    timer: int = Query(default=3 * 60),
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
    channel_name: str = Query(),
    command_prefix: str = Query(default="!!!"),
    max_length: int = Query(default=500, ge=1, le=2000),
    read_username: bool = Query(default=False),
    model: str | None = Query(default=None),
):
    await touch_overlay_usage(channel_name=channel_name)
    return templates.TemplateResponse(
        "overlays/tts.html",
        {
            "request": request,
            "channel_name": channel_name,
            "command_prefix": command_prefix,
            "max_length": max_length,
            "read_username": read_username,
            "model": model or settings.tts_model,
            "tts_speech_url": f"{request.base_url}overlay/tts/speech",
        },
    )


class TTSSpeechSchema(BaseModel):
    input: str
    model: str | None = None
    response_format: str = "mp3"


@router.post("/tts/speech")
async def overlay_tts_speech(
    channel_name: Annotated[str, Query()],
    payload: Annotated[TTSSpeechSchema, Body()],
):
    if channel_name not in {"quantum075", "lul0k"}:
        raise HTTPException(status_code=403, detail="TTS is not enabled for this channel")
    text = payload.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty input")
    token = settings.tts_api_token.get_secret_value()
    if not token:
        raise HTTPException(status_code=503, detail="TTS service is not configured")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                settings.tts_api_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": payload.model or settings.tts_model,
                    "input": text,
                    "response_format": payload.response_format,
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("TTS upstream request failed: %s", exc)
        raise HTTPException(status_code=502, detail="TTS upstream request failed") from exc
    if resp.status_code != 200:
        logger.warning("TTS upstream returned %s: %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=502, detail=f"TTS upstream error: {resp.status_code}")
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "audio/mpeg"))


@router.get("/slovotron")
async def overlay_slovotron(
    request: Request,
    channel_name: str = Query(),
    inactive_timeout: int = Query(default=20),
    inactive_opacity: float = Query(default=0.4),
):
    await touch_overlay_usage(channel_name=channel_name)
    return RedirectResponse(
        url=f"https://slovotron.fra3a.ru/?obs-overlay=1&"
        f"channel={channel_name}&inactive_timeout={inactive_timeout}&"
        f"inactive_opacity={inactive_opacity}&webhook_secret={uuid3(namespace=settings.slovotron_secret, name=channel_name)}&"
        f"webhook_url={request.base_url}api/webhook/slovotron"
    )


@router.get("/star")
async def overlay_star(
    request: Request,
    channel_id: int = Query(),
    pos: float = Query(default=0.5),
    size: int = Query(default=16),
    color: Color = Query(default="#ffd45a"),
    length: float = Query(default=0.39),
    break_chance: float = Query(default=0.015),
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
        },
    )


@router.get("/fireworks")
async def overlay_fireworks(
    request: Request,
    channel_id: int = Query(),
    particle_count: int = Query(default=80, ge=10, le=300),
    decay: float = Query(default=0.015, ge=0.005, le=0.05),
    gravity: float = Query(default=0.08, ge=0.0, le=0.3),
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
        },
    )


@router.get("/start-wait")
async def overlay_start_wait(
    request: Request,
    channel_id: int = Query(),
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
async def overlay_img_gen(
    request: Request,
    channel_id: int = Query(),
    sticker_lifetime: int = Query(default=30),
    sticker_size: int = Query(default=192),
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
async def overlay_img_gen(
    request: Request,
    channel_id: int = Query(),
    widget_type: Literal["pulsma", "battlebeats"] = Query(default="pulsma", alias="widget-type"),
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
    channel_id: int = Query(),
    use_twitch_emoji: bool = Query(default=False),
    arbuz: bool = Query(default=False),
    offset_left: float = Query(0),
    offset_right: float = Query(0),
    offset_top: float = Query(0),
    offset_bottom: float = Query(0),
    card_scale: float = Query(0.7),
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
            items: list[tuple[str, str]] = [
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
