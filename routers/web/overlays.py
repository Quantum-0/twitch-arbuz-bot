import json
import logging
import random
import re
from pathlib import Path
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
from schemas.api import PairCardSchema, PairDeckSchema
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
        url=f"https://slovotron.fra3a.ru/?obs-overlay=1&transparent_background=1&"
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


PairGrid = Literal["3x4", "4x3", "5x4", "4x5", "5x5", "4x4", "3x3", "2x5", "5x2"]
PairTheme = Literal["sunset", "dark", "light", "ocean", "emerald", "violet", "custom"]

PAIR_GRIDS: dict[str, tuple[int, int]] = {
    "3x4": (3, 4),
    "4x3": (4, 3),
    "5x4": (5, 4),
    "4x5": (4, 5),
    "5x5": (5, 5),
    "4x4": (4, 4),
    "3x3": (3, 3),
    "2x5": (2, 5),
    "5x2": (5, 2),
}

# Цвета темы: front1/front2 — градиент рубашки закрытой карточки,
# back1/back2 — градиент открытой, caption — цвет ника, glow — свечение
# найденной пары, shadow — тень карточки (выделяет тёмные темы на тёмном фоне).
PAIR_THEMES: dict[str, dict[str, str]] = {
    "sunset": {
        "front1": "#ff8a00",
        "front2": "#e52e71",
        "back1": "#4facfe",
        "back2": "#00f2fe",
        "caption": "#1c2430",
        "glow": "#ff8a00",
        "shadow": "0 6px 18px rgba(0,0,0,.25)",
    },
    "dark": {
        "front1": "#34343f",
        "front2": "#1d1d26",
        "back1": "#3a3f4a",
        "back2": "#22262e",
        "caption": "#f0f0f5",
        "glow": "#8b93a5",
        "shadow": "0 0 0 1px rgba(255,255,255,.06), 0 6px 18px rgba(0,0,0,.45)",
    },
    "light": {
        "front1": "#e9e9ee",
        "front2": "#cfcfd8",
        "back1": "#f5f5f8",
        "back2": "#e0e0e9",
        "caption": "#3a3a44",
        "glow": "#9aa0ad",
        "shadow": "0 4px 14px rgba(60,60,70,.22)",
    },
    "ocean": {
        "front1": "#2193b0",
        "front2": "#6dd5ed",
        "back1": "#dfe9f3",
        "back2": "#ffffff",
        "caption": "#1f3a4d",
        "glow": "#6dd5ed",
        "shadow": "0 6px 18px rgba(21,54,91,.3)",
    },
    "emerald": {
        "front1": "#11998e",
        "front2": "#38ef7d",
        "back1": "#e8f8f0",
        "back2": "#c9ecd9",
        "caption": "#14532d",
        "glow": "#38ef7d",
        "shadow": "0 6px 18px rgba(10,80,50,.28)",
    },
    "violet": {
        "front1": "#8e2de2",
        "front2": "#4a00e0",
        "back1": "#ece7fb",
        "back2": "#d9c9f5",
        "caption": "#3b2a63",
        "glow": "#a855f7",
        "shadow": "0 6px 18px rgba(70,20,150,.32)",
    },
}

_ARBUZ_CAPTIONS = {
    "1.webp": "Вкусьни",
    "2.webp": "Цвяточк",
    "3.webp": "Жёпь",
    "4.webp": "Играц",
    "5.webp": "Лапк",
    "6.webp": "Смотритб",
    "7.webp": "Думаетб",
    "8.png": "Кексик",
    "9.webp": "Питса",
    "10.webp": "Хвостб",
}

_STICKER_SUFFIXES = {".webp", ".png", ".jpg", ".jpeg", ".gif"}
_STICKERS_DIR = Path(__file__).resolve().parents[2] / "static" / "images" / "stickers"

_PAIR_EMOJIS: list[tuple[str, str]] = [
    ("CorgiDerp", "49106"),
    ("Kappa", "25"),
    ("KomodoHype", "81273"),
    ("KonCha", "160400"),
    ("LUL", "425618"),
    ("NotLikeThis", "58765"),
    ("TwitchConHYPE", "emotesv2_13b6dd7f3a3146ef8dc10f66d8b42a96"),
    ("SeemsGood", "64138"),
    ("PewPewPew", "emotesv2_587405136a8147148c77df74baaa1bf4"),
    ("OSFrog", "81248"),
    ("4Head", "354"),
    ("DansGame", "33"),
    ("EleGiggle", "4339"),
    ("HeyGuys", "30259"),
    ("Kreygasm", "41"),
    ("TriHard", "120232"),
]


def _natural_key(path: Path) -> list[tuple[int, int | str]]:
    # Тег типа части (0=текст, 1=число) не даёт сравнивать str с int
    # на файлах вида "cat.webp" против "1.webp".
    return [(1, int(part)) if part.isdigit() else (0, part) for part in re.split(r"(\d+)", path.name)]


def _arbuz_items() -> list[tuple[str, str, str]]:
    """Стикеры автора бота: (id, подпись, url). Каталог сканируется динамически —
    новые файлы подхватываются без правок кода, подпись неизвестных = имя файла."""
    items: list[tuple[str, str, str]] = []
    if _STICKERS_DIR.is_dir():
        files = [f for f in _STICKERS_DIR.iterdir() if f.suffix.lower() in _STICKER_SUFFIXES]
        for f in sorted(files, key=_natural_key):
            caption = _ARBUZ_CAPTIONS.get(f.name, f.stem)
            items.append((f"arbuz:{f.name}", caption, f"/static/images/stickers/{f.name}"))
    return items


def _emoji_items() -> list[tuple[str, str, str]]:
    return [
        (name, name, f"https://static-cdn.jtvnw.net/emoticons/v2/{eid}/default/dark/4.0") for name, eid in _PAIR_EMOJIS
    ]


def _caption_color_for(background: str) -> str:
    # При alpha=False кортеж всегда RGB, но тип объединён с RGBA — берём по индексам.
    rgb = Color(background).as_rgb_tuple(alpha=False)
    return "#2b2b33" if 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] > 150 else "#f5f5f7"


def _pair_theme_colors(
    theme: PairTheme,
    front1: Color | None,
    front2: Color | None,
    back1: Color | None,
    back2: Color | None,
) -> dict[str, str]:
    if theme != "custom":
        return dict(PAIR_THEMES[theme])
    # Незаданные цвета «своей» темы добираем текущей оранжевой, чтобы ссылка не ломалась.
    colors = dict(PAIR_THEMES["sunset"])
    for key, value in (("front1", front1), ("front2", front2), ("back1", back1), ("back2", back2)):
        if value is not None:
            colors[key] = value.as_hex()
    colors["glow"] = colors["front1"]
    colors["caption"] = _caption_color_for(colors["back1"])
    return colors


async def _pair_streamer_cards(
    db: AsyncSession, channel_id: int, pairs: int, exclude: list[str] | None
) -> list[dict[str, str]]:
    """Колода из юзеров бота: pairs-1 случайных стримеров + владелец канала.
    exclude — логины прошлого раунда (новую игру собираем из новых лиц);
    если с исключением колода не набирается, пробуем без него."""
    randoms = (
        sa.select(User.login_name.label("name"), User.profile_image_url.label("img"))
        .where(User.twitch_id != str(channel_id))
        .where(User.followers_count > 50)
    )
    if exclude:
        randoms = randoms.where(User.login_name.not_in(exclude))
    result = await db.execute(
        sa.union_all(
            randoms.order_by(sa.func.random()).limit(max(pairs - 1, 0)),
            sa.select(User.login_name.label("name"), User.profile_image_url.label("img")).where(
                User.twitch_id == str(channel_id)
            ),
        )
    )
    rows = result.fetchall()
    if exclude and len(rows) < pairs:
        return await _pair_streamer_cards(db, channel_id, pairs, None)
    return [{"id": row.name, "img": row.img, "caption": row.name} for row in rows]


async def _pair_deck(
    db: AsyncSession,
    channel_id: int,
    pairs: int,
    use_twitch_emoji: bool,
    arbuz: bool,
    exclude: list[str] | None = None,
) -> list[dict[str, str]]:
    if arbuz or use_twitch_emoji:
        items = _arbuz_items() if arbuz else _emoji_items()
        picked = random.sample(items, k=min(pairs, len(items)))
        return [{"id": i[0], "caption": i[1], "img": i[2]} for i in picked]
    return await _pair_streamer_cards(db, channel_id, pairs, exclude)


@router.get("/pair")
async def overlay_pair(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    channel_id: Annotated[int, Query()],
    use_twitch_emoji: Annotated[bool, Query()] = False,
    arbuz: Annotated[bool, Query()] = False,
    offset_left: Annotated[float, Query()] = 0.2,
    offset_right: Annotated[float, Query()] = 0.2,
    offset_top: Annotated[float, Query()] = 0.02,
    offset_bottom: Annotated[float, Query()] = 0.05,
    card_scale: Annotated[float, Query()] = 0.87,
    grid: Annotated[PairGrid, Query()] = "5x4",
    theme: Annotated[PairTheme, Query()] = "sunset",
    front1: Annotated[Color | None, Query()] = None,
    front2: Annotated[Color | None, Query()] = None,
    back1: Annotated[Color | None, Query()] = None,
    back2: Annotated[Color | None, Query()] = None,
    show_names: Annotated[bool, Query()] = True,
    restart_delay: Annotated[int, Query(ge=3, le=30)] = 8,
):
    await touch_overlay_usage(channel_id=channel_id)
    cols, rows = PAIR_GRIDS[grid]
    cards = await _pair_deck(
        db,
        channel_id=channel_id,
        pairs=cols * rows // 2,
        use_twitch_emoji=use_twitch_emoji,
        arbuz=arbuz,
    )
    return templates.TemplateResponse(
        "overlays/pair.html",
        {
            "cards": cards,
            "spiral": cols * rows % 2 == 1,
            "offset": {
                "top": offset_top,
                "left": offset_left,
                "bottom": offset_bottom,
                "right": offset_right,
            },
            "card_scale": card_scale,
            "cols": cols,
            "rows": rows,
            "show_names": show_names,
            "restart_delay": restart_delay,
            "theme_colors": _pair_theme_colors(theme, front1, front2, back1, back2),
            "use_twitch_emoji": use_twitch_emoji,
            "arbuz": arbuz,
            "request": request,
            "channel_id": channel_id,
        },
    )


@router.get("/pair/deck")
async def overlay_pair_deck(
    db: Annotated[AsyncSession, Depends(get_db)],
    channel_id: Annotated[int, Query()],
    grid: Annotated[PairGrid, Query()] = "5x4",
    use_twitch_emoji: Annotated[bool, Query()] = False,
    arbuz: Annotated[bool, Query()] = False,
    exclude: Annotated[str | None, Query()] = None,
) -> PairDeckSchema:
    """Свежая колода для перезапуска игры без перезагрузки страницы оверлея."""
    await touch_overlay_usage(channel_id=channel_id)
    cols, rows = PAIR_GRIDS[grid]
    exclude_names = [name.strip().lower() for name in (exclude or "").split(",") if name.strip()]
    cards = await _pair_deck(
        db,
        channel_id=channel_id,
        pairs=cols * rows // 2,
        use_twitch_emoji=use_twitch_emoji,
        arbuz=arbuz,
        exclude=exclude_names or None,
    )
    return PairDeckSchema(cards=[PairCardSchema(**card) for card in cards], spiral=cols * rows % 2 == 1)


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
