from typing import Annotated, Literal
from uuid import uuid3

import sqlalchemy as sa
from dependency_injector.wiring import inject
from fastapi import APIRouter, Query
from fastapi.params import Depends
from pydantic.color import Color
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from config import settings
from database.models import User
from dependencies import get_db

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
):
    return templates.TemplateResponse(
        "overlays/tts.html",
        {
            "request": request,
            "channel_name": channel_name,
        },
    )


@router.get("/slovotron")
async def overlay_slovotron(
    request: Request,
    channel_name: str = Query(),
    inactive_timeout: int = Query(default=20),
    inactive_opacity: float = Query(default=0.4),
):
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


@router.get("/start-wait")
async def overlay_start_wait(
    request: Request,
    channel_id: int = Query(),
):
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
):
    return templates.TemplateResponse(
        "overlays/imggen.html",
        {
            "request": request,
            "channel_id": channel_id,
        },
    )


@router.get("/ya-music-widget")
async def overlay_img_gen(
    request: Request,
    channel_id: int = Query(),
    widget_type: Literal["pulsma", "battlebeats"] = Query(default="pulsma", alias="widget-type"),
):
    return templates.TemplateResponse(
        f"overlays/ya-music-widget-{widget_type}.html",
        {
            "request": request,
            "channel_id": channel_id,
        },
    )


@router.get("/pair")
@inject
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
