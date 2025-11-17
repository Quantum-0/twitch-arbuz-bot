import random
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Security
from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, FileResponse
from starlette.templating import Jinja2Templates

from config import settings
from database.models import TwitchUserSettings, User
from dependencies import get_chat_bot, get_db, get_twitch
from routers.security_helpers import admin_auth, user_auth, user_auth_optional
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from twitch.state_manager import get_state_manager
from utils.memes import token_expires_in_days

templates = Jinja2Templates(directory="templates")

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/panel")
    else:
        return RedirectResponse(url="/login")


@router.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.ico")


@router.get("/login")
async def login():
    # TODO: Добавить страничку с кнопочкой "авторизоваться через твич
    return RedirectResponse(settings.login_twitch_url)


@router.get("/panel")
async def main_page(
    request: Request,
    user: User = Security(user_auth),
):
    if not user.in_beta_test:
        return templates.TemplateResponse("beta-test.html", {"request": request})
    return templates.TemplateResponse(
        "index.html",
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
async def debug_page(
    request: Request,
    chat_bot: Annotated[ChatBot, Depends(get_chat_bot)],
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
async def command_list_page(
    request: Request,
    streamer: Annotated[str, Query(...)],
    db: Annotated[AsyncSession, Depends(get_db)],
    chat_bot: Annotated[ChatBot, Depends(get_chat_bot)],
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
async def get_streamers(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User | None = Security(user_auth_optional),
):
    q = (
        sa.select(
            User.login_name.label("username"),
            User.profile_image_url.label("avatar_url"),
            User.followers_count.label("followers"),
            User.in_beta_test.label("is_beta_tester"),
            User.in_beta_test.label("donated"),
        )
        .where(User.followers_count > 10)
        .limit(400)
    )
    res = list((await db.execute(q)).fetchall())
    random.shuffle(res)
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

    res = [row._asdict() for row in res]
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
            ],
            "todos": [
                "Добавить свою команду !трусы из Mix It Up",
                'Для команды !трусы сделать возможность отказаться, если пользователь пишет минус, предлагаем: "!отказаться сейчас/тут/везде" - конкретный розыгрыш, канал или все каналы',
                "Сделать ручку для оверлеев и возможность их настройки",
                "Добавить !куст в !кусь",
                "В список стримеров добавить роли: разраб, донатер, бета-тестер, остальные",
                "/streamers сортировка по (role, random)",
                "Target-команды должны работать с реплаями",
                "можем ли определить что сообщение из другого чата в кооп стриме? если да - не отвечать (опционально?)",
            ],
            "request": request,
            "user": user,
        },
    )


@router.get("/login-callback")
async def callback(
    request: Request,
    code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(get_twitch)],
):
    (
        access_token,
        refresh_token,
    ) = await twitch.get_user_access_refresh_tokens_by_authorization_code(code)
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

    followers = await twitch.get_followers(user)
    await twitch.set_bot_moder(user)
    user.followers_count = followers.total
    await db.commit()

    request.session["user_id"] = user_id
    return RedirectResponse(url="/panel")
