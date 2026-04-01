import asyncio
import logging
import math
import random
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urljoin
from uuid import UUID

import httpx
import sqlalchemy as sa
from bs4 import BeautifulSoup
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, HTTPException, Query, Security
from fastapi.params import Depends
from pydantic.color import Color
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, FileResponse
from starlette.templating import Jinja2Templates

from config import settings
from database.models import TwitchUserSettings, User, MemealertsSettings
from container import Container
from dependencies import get_db
from routers.security_helpers import admin_auth, user_auth, user_auth_optional
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from twitch.state_manager import get_state_manager
from utils.memes import token_expires_in_days

templates = Jinja2Templates(directory="templates")

router = APIRouter()


@router.get("/profile/{profile_user:str}")
@inject
async def profile_page(
    profile_user: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User | None = Security(user_auth_optional),
):
    profile_user_data = (await db.execute(
        sa.select(User)
        .options(selectinload(User.settings))
        .options(selectinload(User.memealerts))
        .filter_by(login_name=profile_user)
    )).scalar_one_or_none()
    profile_user_dict = profile_user_data.__dict__
    streams = await twitch.get_streams([profile_user_data])
    profile_user_dict["is_live"] = bool(streams.get(profile_user_data))
    # profile_user_dict["link_telegram"] = "t.me/quantum0"
    # profile_user_dict["link_discord"] = "discord.com"
    profile_user_dict["memealerts_enabled"] = profile_user_data.memealerts.memealerts_reward is not None
    return templates.TemplateResponse(
        "profile.html",
        {
            "user": user,
            "profile_user": profile_user_dict,
            "request": request,
        }
    )


@router.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/panel")
    else:
        return templates.TemplateResponse(
            "main.html",
            {
                "request": request,
            }
        )


@router.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.ico")


@router.get("/overlay/jumping-chibi")
async def overlay_jumping_chibi(
    request: Request,
    timer: int = Query(default=3*60),
):
    return templates.TemplateResponse(
        "overlays/jumping-chibi.html",
        {
            "request": request,
            "timer": timer * 1000,
        }
    )


@router.get("/overlay/tts")
async def overlay_tts(
    request: Request,
    channel_name: str = Query(),
):
    return templates.TemplateResponse(
        "overlays/tts.html",
        {
            "request": request,
            "channel_name": channel_name,
        }
    )


@router.get("/overlay/slovotron")
async def overlay_slovotron(
    channel_name: str = Query(),
):
    async with httpx.AsyncClient() as client:
        # TODO: webhook - победа, подсказка. utm метки
        response = await client.get("https://slovotron.fra3a.ru?obs-overlay=1&channel=" + channel_name)
        soup = BeautifulSoup(response.text, 'lxml')
        # undesired_elements = soup.find_all('nav') + soup.find_all('footer') + [soup.select_one("#info.content-box")]
        # for el in undesired_elements:
        #     if el:
        #         style = el.get("style", "")
        #         if not style.endswith(";") and style != "":
        #             style += ";"
        #         el["style"] = style + "display: none;"
        for tag in soup.find_all(["a", "link", "script", "img"]):
            attr = "href" if tag.name in ["a", "link"] else "src"
            if tag.has_attr(attr):
                tag[attr] = urljoin("https://slovotron.fra3a.ru/", tag[attr])
    return HTMLResponse(soup.prettify())


@router.get("/overlay/star")
async def overlay_star(
    request: Request,
    channel_id: int = Query(),
    pos: float = Query(default=0.5),
    size: int = Query(default=16),
    color: Color = Query(default="#ffd45a"),
    length: float = Query(default=0.39),
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
        }
    )


@router.get("/overlay/start-wait")
async def overlay_start_wait(
    request: Request,
    channel_id: int = Query(),
):
    return templates.TemplateResponse(
        "overlays/start_wait.html",
        {
            "request": request,
            "channel_id": channel_id,
        }
    )


@router.get("/overlay/ai-sticker")
async def overlay_img_gen(
    request: Request,
    channel_id: int = Query(),
):
    return templates.TemplateResponse(
        "overlays/imggen.html",
        {
            "request": request,
            "channel_id": channel_id,
        }
    )


@router.get("/overlay/pair")
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
                sa.select(
                    User.login_name.label("name"),
                    User.profile_image_url.label("img")
                )
                .where(User.twitch_id != str(channel_id))
                .where(User.followers_count > 50)
                .order_by(sa.func.random())
                .limit(9),
                sa.select(
                    User.login_name.label("name"),
                    User.profile_image_url.label("img")
                )
                .where(User.twitch_id == str(channel_id))
            )
        )
        cards = [
            {"id": row.name, "img": row.img, "caption": row.name}
            for row in result.fetchall()
        ]
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
                ("TwitchConHYPE", "https://static-cdn.jtvnw.net/emoticons/v2/emotesv2_13b6dd7f3a3146ef8dc10f66d8b42a96/default/dark/4.0"),
                ("SeemsGood", "https://static-cdn.jtvnw.net/emoticons/v2/64138/default/dark/4.0"),
                ("PewPewPew", "https://static-cdn.jtvnw.net/emoticons/v2/emotesv2_587405136a8147148c77df74baaa1bf4/default/dark/4.0"),
                ("OSFrog", "https://static-cdn.jtvnw.net/emoticons/v2/81248/default/dark/4.0"),
            ]
        cards = [
            {"id": item[0], "img": item[1], "caption": item[0]}
            for item in items
        ]
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
        }
    )


@router.get("/login")
async def login():
    # TODO: Добавить страничку с кнопочкой "авторизоваться через твич
    return RedirectResponse(settings.login_twitch_url)


@router.get("/panel")
async def control_panel(
    request: Request,
    user: User = Security(user_auth),
):
    # if not user.in_beta_test:
    #     return templates.TemplateResponse("beta-test.html", {"request": request})
    return templates.TemplateResponse(
        "panel.html",
        {
            "request": request,
            "user": user,
            "settings": user.settings,
            "memealerts": {
                "enabled": user.memealerts.memealerts_reward is not None,
                "expires_in": await token_expires_in_days(
                    user.memealerts.memealerts_token
                )
                if user.memealerts.memealerts_token
                else None,
                "coins_for_reward": user.memealerts.coins_for_reward,
            },
        },
    )


@router.get("/about")
async def about_page(
    request: Request,
    user: User | None = Security(user_auth_optional),
):
    return templates.TemplateResponse(
        "about.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.get("/memealerts-tutorial")
async def meme_tutorial_page(
    request: Request,
    user: User | None = Security(user_auth_optional),
):
    return templates.TemplateResponse(
        "memealerts-tutorial.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.get("/debug")
@inject
async def debug_page(
    request: Request,
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    user: Any = Security(user_auth),
):
    if not user.in_beta_test:
        raise HTTPException(status_code=403, detail="No access to debug")
    state_manager_data = []
    async for values in get_state_manager().get_all_from_channel(channel="quantum075"):
        state_manager_data.append(values)
    async for values in get_state_manager().get_all_from_channel():
        state_manager_data.append(values)
    return templates.TemplateResponse(
        "debug.html",
        {
            "user": user,
            "request": request,
            "state_manager_data": state_manager_data,
            "last_active": await chat_bot.get_last_active_users(user),
        },
    )


@router.get("/admin")
async def admin_page(
    request: Request,
    user: Any = Security(user_auth),
    _: None = Security(admin_auth),
):
    if not user.login_name == "quantum075":
        raise HTTPException(status_code=403, detail="No access to admin panel")
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.get("/cmdlist")
@inject
async def command_list_page(
    request: Request,
    streamer: Annotated[str, Query(...)],
    db: Annotated[AsyncSession, Depends(get_db)],
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    # streamer_id: int = Query(...),
    user: User | None = Security(user_auth_optional),
):
    result = await db.execute(
        # sa.select(User).options(selectinload(User.settings)).filter_by(twitch_id=str(streamer_id))
        sa.select(User)
        .options(selectinload(User.settings))
        .filter_by(login_name=streamer)
    )
    streamer_user = result.scalar_one_or_none()
    if not streamer_user:
        return HTTPException(404, "Streamer not found")
    user_settings: TwitchUserSettings = streamer_user.settings
    return templates.TemplateResponse(
        "streamer-commands.html",
        {
            "user": user,
            "request": request,
            "streamer_name": streamer_user.login_name,
            "streamer_pic": streamer_user.profile_image_url,
            "commands": await chat_bot.get_commands(streamer_user),
        },
    )


@router.get("/streamers")
@inject
async def get_streamers(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User | None = Security(user_auth_optional),
):
    q = (
        sa.select(
            User.login_name.label("username"),
            User.profile_image_url.label("avatar_url"),
            User.followers_count.label("followers"),
            User.in_beta_test.label("is_beta_tester"),
            User.donated.label("donated"),
            TwitchUserSettings.enable_chat_bot.label("chat_bot_enabled"),
            MemealertsSettings.memealerts_reward.is_not(None).label("memealerts_enabled")
        )
        .select_from(User)
        .join(TwitchUserSettings)
        .join(MemealertsSettings)
        .where(User.followers_count > 2)
        .limit(500)
    )
    res = (await db.execute(q)).all()

    def cmp(usr):
        return (
            (10 * bool(usr["is_live"])) +
            (0.6 * math.log10((usr.get("followers", 0) or 0) + 1)) +
            (2 * (usr["username"] == "quantum075" or usr["donated"] > 0)) +
            (1 * usr["is_beta_tester"]) +
            (2 * usr["memealerts_enabled"]) +
            (3 * usr["chat_bot_enabled"])
            + (5 * random.random())
        )

    res = [row._asdict() for row in res]
    streams = await twitch.get_streams([row["username"] for row in res])
    for row in res:
        row["is_live"] = streams.get(row["username"])
        row["score"] = cmp(row)

    res = sorted(res, key=cmp, reverse=True)

    # res = [row._asdict() for row in res] * 20
    # for row in res:
    #     row["followers"] = random.randint(30, 500)
    #
    # # Перемешиваем для случайного порядка
    # random.shuffle(res)
    #
    # # Размер пузырька в зависимости от фолловеров
    # max_followers = max(s["followers"] for s in res) or 1
    # min_size, max_size = 20, 150
    # for s in res:
    #     ratio = s["followers"] / max_followers
    #     s["size"] = int(min_size + (max_size - min_size) * ratio)

    for row in res:
        row["role"] = "beta" if row["is_beta_tester"] else None
        if row["username"] == "quantum075":
            row["role"] = "dev"
        if row["donated"] > 0:
            row["role"] = "donater"
    return templates.TemplateResponse(
        "streamers.html", {"request": request, "streamers": res, "user": user}
    )


@router.get("/kinda_roadmap")
async def roadmap_page(
    request: Request,
    user: User | None = Security(user_auth_optional),
):
    return templates.TemplateResponse(
        "roadmap.html",
        {
            "roadmap": [
                {
                    "date": "Апрель-Май 2025",
                    "text": "Идея об интеграции Memealerts в Twitch, добавление награды для начисления мемкоинов через баллы канала через Mix It Up.",
                },
                {
                    "date": "Июнь-Июль 2025",
                    "text": "Создание библиотечки для Python для взаимодействия с Memealerts.",
                },
                {
                    "date": "Начало июля 2025",
                    "text": "Идея о переносе команд чат-бота из Mix It Up и Stream Elements в собственный сервис и интеграция туда Memealerts для бóльшей стабильности.",
                },
                {
                    "date": "13 июля 2025",
                    "text": "Первая версия сервиса. Реализована авторизация через Twitch, интеграция бота в чат с 3 простыми командами и база данных для их включения/выключения.",
                },
                {
                    "date": "17-19 июля 2025",
                    "text": "Получение данных от Twitch о срабатывании наград на канале. Переработка скрипта из Mix It Up, подключение ранее реализованной библиотеки memealerts. Обновление дизайна сайта, переработка моделей, мониторинг ошибок.",
                },
                {
                    "date": "20 июля 2025",
                    "text": "Переработка взаимодействия с БД, ускорение обработки ответов. Отображение времени жизни токена Memealerts, автоматическое подтверждение или отклонение награды для возврата баллов. Исправление ошибок.",
                },
                {
                    "date": "27-31 июля 2025",
                    "text": "Полная переработка логики обработки команд в чате. Инструкция по подключению Memealerts. Добавление списка стримеров, использующих бота. Добавление новых команд в чат бота.",
                },
                {
                    "date": "27-31 июля 2025",
                    "text": "Больше команд. Админка. Авто-шаутаут на рейды. Исправление ошибок",
                },
                {
                    "date": "16 августа 2025",
                    "text": "Добавление роадмапа. Уже 20 пользователей - бета-тестеров.",
                },
                {
                    "date": "23 августа 2025",
                    "text": "Бот научился видеть реплаи и отвечать на пару сообщений адресованых ему.",
                },
                {
                    "date": "Сентябрь 2025",
                    "text": "Полная переработка системы сообщений на новый API твича. Интеграция тестов в проект.",
                },
                {
                    "date": "Октябрь-ноябрь 2025",
                    "text": "Рефакторинг кода, обработка 500-ой ошибки, возможность выбора случайного"
                            " пользователя в некоторых командах, обновление безопасности, новые команды",
                },
                {
                    "date": "Ноябрь 2025",
                    "text": "Много мелких исправлений, отдельная страничка для внутренней ошибки сайта, иконка сайта."
                            "добавлена возможность указания рандомного пользователя для команд, исправлена генерация рандома"
                            "для !хорни и !хвост. Новая команда !dice. Множество новых вариантов ответов для всех команд"
                },
                {
                    "date": "Декабрь 2025",
                    "text": "НАКОНЕЦ добавлена команад !трусы (ради которой я ушёл от прочих чат-ботов и создал своего). "
                            "Добавлена команда !якто. Добавлена возможность отказаться от участия в команде !трусы с помощью !запреттрусов"
                },
                {
                    "date": "Январь 2026",
                    "text": "Добавлен функционал с интерактивыми оверлеями. "
                            "Бот выходит из бета-теста, пользоваться ботом могут все желающие."
                            "Добавил оверлей со звёздочкой. Добавил обфускацию кода. Доработаю подключение к плагину Heat."
                },
                {
                    "date": "Февраль 2026",
                    "text": "Начал работу над AI Stickers. Подключил очередь сообщений для развязки компонентов сервиса. "
                            "Новые команды: !вкусняшка !паста и !покормить. "
                            "Переработал визуально интерфейс сайта. Добавлена тёмная тема и эффект Liquid Glass. "
                            "Добавил кастомизацию для звёздочки, игры в пары. Улучшил стабильность работы с Heat. "
                            "Получил значок бота для учётки бота в чате твича."
                },
                {
                    "date": "Март 2026",
                    "text": "Добавил словотрон. Добавил e2e тесты. Добавил страничку профиля пользователя. "
                            "Улучшим стабильность Heat. Добавил автоматическое исправление EventSub (если перестают работать награды или шаутаут на рейды). "
                            "Добавил обновление имени пользователя при перелогине. Подключил Dependency Injector. Добавил искорки к звёздочке."
                },
            ],
            "todos": [
                "Отчёт об ошибках из мемалёрта в панели управления ботом",
                "Для команды !трусы - можно попробовать хранить кто чьи забрал, между каналами, куда-нибудь выводить статистику",
                "Игнорировать сообщения из других каналов при участии в кооп стримах",
                "Переработать /streamers, разделить на категории: разработка и непосредственная помощь с ботом,"
                " финансовая поддержка, бета тестеры (рандомные N штук), "
                " самые активные стримеры (табличка activity_streamer, "
                "user + count, count++ при каждой команде или награде), "
                "самые активные юзеры. Так же проверять текущий статус онлайна"
                "отвечать на 'спасибо', 'пожалуйста', 'дай мем(коин)?ов'",
                "Дать возможность пользователям поменять дефолтное поведение команд с целью на выбор рандома",
            ],
            "request": request,
            "user": user,
        },
    )


@router.get("/login-callback")
@inject
async def callback(
    request: Request,
    code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
):
    tokens = await twitch.get_user_access_refresh_tokens_by_authorization_code(code)
    if tokens is None:
        return RedirectResponse(url="/")

    (
        access_token,
        refresh_token,
    ) = tokens
    user_info = await twitch.get_self(access_token, refresh_token)

    user_id = user_info.id
    login_name = user_info.login
    profile_image_url = user_info.profile_image_url

    result = await db.execute(sa.select(User).filter_by(twitch_id=user_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            twitch_id=user_id,
            login_name=login_name,
            profile_image_url=profile_image_url,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        db.add(user)
    else:
        user.access_token = access_token
        user.refresh_token = refresh_token
        user.profile_image_url = profile_image_url
        user.login_name = login_name
        db.add(user)

    await db.commit()
    asyncio.create_task(login_callback_task(user))

    request.session["user_id"] = user_id
    return RedirectResponse(url="/panel")


logger = logging.getLogger(__name__)

# TODO: сделать бэкграунд таской штоле о_О
@inject
async def login_callback_task(
    user: User,
    twitch: Annotated[Twitch, Provide[Container.twitch]],
    db_session_factory: Annotated[Callable[[], AsyncSession], Provide[Container.db_session_factory]],
):
    # Получаем фолловеров
    followers = await twitch.get_followers(user)

    # Делаем бота модератором
    await twitch.set_bot_moder(user)

    async with db_session_factory() as session:
        # Обновляем фолловеров
        q = sa.update(User).values(followers_count=followers.total).where(User.twitch_id == user.twitch_id)
        await session.execute(q)
        await session.commit()

        # Получаем настройки мемалёртов, рейдов и прочих ревардов
        res = await session.execute(
            sa.select(User)
            .options(
                selectinload(User.settings),
                selectinload(User.memealerts),
            )
            .filter_by(twitch_id=user.twitch_id)
        )
        user = res.scalar_one_or_none()
        shoutout_to_raid_is_enabled = user.settings.enable_shoutout_on_raid
        memealerts_reward = user.memealerts.memealerts_reward
        ai_stickers_reward = user.settings.ai_sticker_reward_id

    if shoutout_to_raid_is_enabled is False and memealerts_reward is None and ai_stickers_reward is None:
        return

    subs = await twitch.get_subscriptions()

    subs_for_rewards = [sub for sub in subs if sub.type == "channel.channel_points_custom_reward_redemption.add" and sub.condition.get(
        "broadcaster_user_id") == user.twitch_id]

    subs_for_raid = [sub for sub in subs if sub.type == "channel.raid"  and sub.condition.get("to_broadcaster_user_id") == user.twitch_id]

    # TODO: unsubscribe from unused subs

    if shoutout_to_raid_is_enabled and not subs_for_raid:
        logger.warning(f"Found missing raid eventsub for user `{user}`. Re-subscribed!")
        await twitch.subscribe_raid(user)
    elif not shoutout_to_raid_is_enabled and subs_for_raid:
        await twitch.unsubscribe_raid(subscription_id=UUID(subs_for_raid[0].id))

    for sub in subs_for_rewards:
        if sub.condition.get("reward_id") not in {str(ai_stickers_reward), str(memealerts_reward)}:
            logger.warning(f"Found extra eventsubs for user `{user}`. Unsubscribing..")
            await twitch.unsubscribe_event_sub(sub.id)

    sub_for_memecoins_exist = any(sub for sub in subs_for_rewards if sub.condition.get("reward_id") == str(ai_stickers_reward))
    sub_for_ai_stickers_exist = any(
        sub for sub in subs_for_rewards if sub.condition.get("reward_id") == str(ai_stickers_reward))

    if memealerts_reward and not sub_for_memecoins_exist:
        logger.warning(f"Found missing memealerts reward eventsub for user `{user}`. Re-subscribed!")
        await twitch.subscribe_reward(user, memealerts_reward)

    if ai_stickers_reward and not sub_for_ai_stickers_exist:
        logger.warning(f"Found missing ai sticker reward eventsub for user `{user}`. Re-subscribed!")
        await twitch.subscribe_reward(user, ai_stickers_reward)

    logger.info(f"Post-login validation for user {user} is done!")