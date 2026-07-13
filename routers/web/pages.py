import logging
import math
import random
from datetime import datetime
from typing import Annotated, Any
from uuid import uuid3

import aiohttp
import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, HTTPException, Query, Security
from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from config import settings
from container import Container
from database.models import TwitchUserSettings, User, MemealertsSettings, GeneratedImage
from dependencies import get_db
from routers.security_helpers import admin_auth, user_auth, user_auth_optional
from services.cache import Cache
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from twitch.state_manager import StateManager
from utils.memes import token_expires_in_days

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="", tags=["Basic Front-end"])


logger = logging.getLogger(__name__)


@router.get(
    "/",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Главная страница сайта"},
        307: {"description": "Переход в панель управления ботом"},
    },
)
async def index_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/panel")
    else:
        return templates.TemplateResponse(
            "main.html",
            {
                "request": request,
            },
        )


@router.get(
    "/panel",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Панель управления ботом"},
        307: {"description": "Возврат на главную страницу, если не авторизован"},
    },
)
async def control_panel(
    request: Request,
    user: User | None = Security(user_auth_optional),
):
    # if not user.in_beta_test:
    #     return templates.TemplateResponse("beta-test.html", {"request": request})
    if not user:
        return RedirectResponse("/")
    return templates.TemplateResponse(
        "panel.html",
        {
            "request": request,
            "user": user,
            "settings": user.settings,
            "memealerts": {
                "enabled": user.memealerts.memealerts_reward is not None,
                "expires_in": await token_expires_in_days(user.memealerts.memealerts_token)
                if user.memealerts.memealerts_token
                else None,
                "coins_for_reward": user.memealerts.coins_for_reward,
                "enabled_v2": user.memealerts.access_token is not None,
            },
            "slovotron_secret": str(uuid3(namespace=settings.slovotron_secret, name=user.login_name)),
        },
    )


@router.get(
    "/faq",
    response_class=HTMLResponse,
    responses={
        200: {"description": "FAQ"},
    },
)
async def read_faq(request: Request):
    faq_items = [
        {
            "question": "Какие функции есть у сервиса?",
            "answer": "Выдача мемкоинов с сервиса Memealerts за баллы канала, чат-бот для вашего канала Twitch, интерактивные оверлеи для OBS, словотрон, автоматический shoutout на рейды на Twitch, ИИ-стикеры",
        },
        {
            "question": "Что происходит после авторизации в сервисе?",
            "answer": "Бот с ником Quantum075Bot получит права модератора на вашем канале и роль бота.",
        },
        {
            "question": "Зачем боту права модератора?",
            "answer": "Бот добавлен на множество каналов, читает большое количество сообщений и отвечает на адресованные ему команды. Без роли модератора, твич разрешает боту отвечать не более чем на 60 сообщений в минуту для ВСЕХ каналов, а с учётом количества стримеров, использующих бота - скорость его ответов может опуститься до 1 сообщения в минуту. Поэтому боту требуется роль модератора, чтоб Twitch не ограничивал его возможность писать в чат.",
        },
        {
            "question": "Как работает чат-бот?",
            "answer": "После того как вы включите чатбот, учётная запись @Quantum075Bot подключится к вашему каналу и будет читать сообщения. Вы сами выбираете какие команды будут работать в чате.",
        },
        {
            "question": "Как посмотреть список команд бота?",
            "answer": "На канале, где подключён данный бот, вы можете написать !cmdlist и получить ссылку на страницу со списком всех доступных в чате команд.",
        },
        {
            "question": "Как работает выдача мемкоинов?",
            "answer": "Вы подключаете интеграцию с Memealerts, после чего зрители смогут покупать мемкоины за баллы канала. Выдача будет автоматической. Важно: для получения мемкоинов зритель ОБЯЗАТЕЛЬНО должен получить приветственный бонус на мемалертсе."
        },
        {
            "question": "Как подключить автоматическую выдачу мемкоинов на Twitch?",
            "answer": "Перейдите на сайт сервиса bot.quantum0.ru, авторизуйтесь через твич, перейдите в раздел \"настройка мемалёртов\" в панели управления ботом, вставьте токен с сервиса Memealerts. После этого у вас появится соответствующая награда на Twitch.",
        },
        {
            "question": "Могу ли я переименовать награду для автоматической выдачи мемкоинов?",
            "answer": "Да, вы можете менять название, описание, цену и изображение награды. Пропускать очередь на обработку наград выключать не нужно - бот определяет, успешно ли выданы мемкоины. В случае успеха - сам помечает награду выполненно, а в случае ошибки - возвращает баллы канала зрителю."
        },
        {
            "question": "Как настроить команды !паста, !тг, !дис?",
            "answer": "Эти команды сохраняют значение, введённое стримером, после чего пользователи могут вызывать её, и бот будет выдавать сохранённое ранее значение. То есть стример пишет: \"!паста всем кусь\", после этого зрители пишут \"!паста\" и получают от бота ответ: \"всем кусь\"",
        },
        {
            "question": "Насколько сервис безопаснен?",
            "answer": "Вход в аккаунт происходит полностью на стороне твича. Данный сайт не имеет доступа к вашему логину и паролю. Данные, связанные с вашим аккаунтом Twitch и Memealerts сохраняются во внутренней базе данных, и шифруются дополнительным ключом безопасности, поэтому даже в случае взлома сайта и базы данных, доступ к вашим аккаунтам останется в сохранности.",
        },
    ]

    return templates.TemplateResponse(
        "faq.html",
        {"request": request, "faq_items": faq_items}
    )


@router.get(
    "/profile/{profile_user:str}",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Страница профиля пользователя"},
        404: {"description": "Профиль не найден"},
    },
)
@inject
async def profile_page(
    profile_user: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: User | None = Security(user_auth_optional),
):
    profile_user_data: User = (  # type: ignore
        await db.execute(
            sa.select(User)
            .options(
                joinedload(User.settings),
                joinedload(User.memealerts),
                joinedload(User.links)
            )
            .filter_by(login_name=profile_user)
        )
    ).scalar_one_or_none()
    q = (
        sa.select(GeneratedImage)
        .where(GeneratedImage.on_channel == int(profile_user_data.twitch_id))
        .order_by(GeneratedImage.created_at.desc())
        .limit(100)  # TODO: сделать потом отдельную страницу, где можно будет посмотреть все генерации, с пагинацией
    )
    ai_stickers = (await db.execute(q)).scalars().all()
    await db.commit()
    if not profile_user_data:
        raise HTTPException(404, "User not found")
    profile_user_dict = profile_user_data.__dict__
    streams = await cache.as_cached(twitch.get_streams, [profile_user_data])
    followers_count = await cache.as_cached(twitch.get_followers_count, profile_user_data)
    profile_user_dict["is_live"] = set(streams.values()) != {None}
    # TODO
    # profile_user_dict["followers_count"] = followers_count
    # AND SAVE TO DB
    profile_user_dict["memealerts_enabled"] = profile_user_data.memealerts.memealerts_reward is not None
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.get(
                "https://twitchtracker.com/api/channels/summary/" + profile_user_data.login_name
            ) as http_resp:
                rank_data = await http_resp.json()
                profile_user_dict["tt_rank"] = rank_data.get("rank")
    except:
        pass
    return templates.TemplateResponse(
        "profile.html",
        {"user": user, "profile_user": profile_user_dict, "request": request, "ai_stickers": ai_stickers or None},
    )


@router.get(
    "/about",
    response_class=HTMLResponse,
)
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


@router.get(
    "/memealerts-tutorial",
    response_class=HTMLResponse,
)
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


@router.get(
    "/debug",
    response_class=HTMLResponse,
)
@inject
async def debug_page(
    request: Request,
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    state_manager: Annotated[StateManager, Depends(Provide[Container.state_manager])],
    user: Any = Security(user_auth),
):
    if not user.in_beta_test:
        raise HTTPException(status_code=403, detail="No access to debug")
    state_manager_data = []
    async for values in state_manager.get_all_from_channel(channel="quantum075"):
        state_manager_data.append(values)
    async for values in state_manager.get_all_from_channel():
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


@router.get(
    "/admin",
    response_class=HTMLResponse,
)
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


@router.get(
    "/cmdlist",
    response_class=HTMLResponse,
)
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
        sa.select(User).options(selectinload(User.settings)).filter_by(login_name=streamer)
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


@router.get(
    "/streamers",
    response_class=HTMLResponse,
)
@inject
async def get_streamers(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
    user: User | None = Security(user_auth_optional),
):
    q = (
        sa.select(
            User.login_name.label("username"),
            User.profile_image_url.label("avatar_url"),
            User.followers_count.label("followers"),
            User.in_beta_test.label("is_beta_tester"),
            User.donated.label("donated"),
            User.created_at.label("created_at"),
            User.interacted_at.label("interacted_at"),
            TwitchUserSettings.enable_chat_bot.label("chat_bot_enabled"),
            MemealertsSettings.memealerts_reward.is_not(None).label("memealerts_enabled"),
        )
        .select_from(User)
        .join(TwitchUserSettings)
        .join(MemealertsSettings)
        .where(User.followers_count > 2)
        .limit(500)
    )
    res = (await db.execute(q)).all()

    def cmp(usr):
        now = datetime.now()
        day = 24 * 60 * 60
        return (
            (10 * bool(usr["is_live"]))
            + (4 * bool((now - usr["created_at"]).total_seconds() < day))  # Зареган меньше суток назад
            + (1.75 * bool((now - usr["created_at"]).total_seconds() < 7 * day))  # Зареган меньше недели назад
            + (
                1.75 * bool((now - usr["interacted_at"]).total_seconds() < 30 * day)
            )  # Заходил в панель управления ботом за последний месяц
            + (
                2 * bool((now - usr["interacted_at"]).total_seconds() < day)
            )  # Заходил в панель управления ботом за последние сутки
            + (0.6 * math.log10((usr.get("followers", 0) or 0) + 1))
            + (2 * (usr["username"] == "quantum075" or usr["donated"] > 0))
            + (1 * usr["is_beta_tester"])
            + (2 * usr["memealerts_enabled"])
            + (3 * usr["chat_bot_enabled"])
            + (5 * random.random())
        )

    res = [row._asdict() for row in res]
    online_streams = await cache.get_set("online_streams")
    if not online_streams:
        streams = await twitch.get_streams([row["username"] for row in res])
        online_streams = {row["username"] for row in res if streams.get(row["username"])}
        await cache.set_set("online_streams", online_streams, ttl=300)
        logger.info("Online streamers list loaded from twitch")
    else:
        logger.info("Online streamers list loaded from cache")
    for row in res:
        row["is_live"] = row["username"] in online_streams
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
    return templates.TemplateResponse("streamers.html", {"request": request, "streamers": res, "user": user})


@router.get(
    "/kinda_roadmap",
    response_class=HTMLResponse,
)
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
                    "для !хорни и !хвост. Новая команда !dice. Множество новых вариантов ответов для всех команд",
                },
                {
                    "date": "Декабрь 2025",
                    "text": "НАКОНЕЦ добавлена команад !трусы (ради которой я ушёл от прочих чат-ботов и создал своего). "
                    "Добавлена команда !якто. Добавлена возможность отказаться от участия в команде !трусы с помощью !запреттрусов",
                },
                {
                    "date": "Январь 2026",
                    "text": "Добавлен функционал с интерактивыми оверлеями. "
                    "Бот выходит из бета-теста, пользоваться ботом могут все желающие."
                    "Добавил оверлей со звёздочкой. Добавил обфускацию кода. Доработаю подключение к плагину Heat.",
                },
                {
                    "date": "Февраль 2026",
                    "text": "Начал работу над AI Stickers. Подключил очередь сообщений для развязки компонентов сервиса. "
                    "Новые команды: !вкусняшка !паста и !покормить. "
                    "Переработал визуально интерфейс сайта. Добавлена тёмная тема и эффект Liquid Glass. "
                    "Добавил кастомизацию для звёздочки, игры в пары. Улучшил стабильность работы с Heat. "
                    "Получил значок бота для учётки бота в чате твича.",
                },
                {
                    "date": "Март 2026",
                    "text": "Добавил словотрон. Добавил e2e тесты. Добавил страничку профиля пользователя. "
                    "Улучшим стабильность Heat. Добавил автоматическое исправление EventSub (если перестают работать награды или шаутаут на рейды). "
                    "Добавил обновление имени пользователя при перелогине. Подключил Dependency Injector. Добавил искорки к звёздочке.",
                },
                {
                    "date": "Апрель 2026",
                    "text": "Отрывание звёздочки. Фикс трусов. Команды !тг и !дис, вывод их в профиль. Добавил оверлей яндекс-музыки.",
                },
                {
                    "date": "Май 2026",
                    "text": "Вебхук для словотрона. AI стикеры переключены на S3. AI стикеры теперь умеют работать со ссылками на стримеров, подтягивая их рефы. "
                            "Сортировка стримеров по их последнему активу в панели управления ботом. Оптимизирован запрос в мемалёртс, добавлен кэш."
                            "Убраны дубликаты ответов для общих чатов.",
                },
                {
                    "date": "Июнь 2026",
                    "text": "Теперь команды умеют использовать алиас `себя` (напр. `!кусь себя`)"
                            "Исправлены мелкие баги в ответах. Переделана логика алиаса `всех`."
                            "Хранение временных данных, таких как cooldown по командам перенесено в Redis."
                            "Добавил в профиль ссылку на МА. Оптимизация скорости работы сервиса."
                            ""
                }
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
