import base64
import logging
import random
import re
from typing import Annotated

import jwt
import sqlalchemy as sa
from dependency_injector.wiring import Provide
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from container import Container
from database.models import MemealertsSettings, TwitchUserSettings, User
from dependencies import get_db
from services.cache import Cache
from twitch.client.twitch import Twitch
from utils.streamers_sort import compute_streamer_score

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extension", tags=["Extension API"])

PFP = {
    "Quantum075Bot": "https://static-cdn.jtvnw.net/jtv_user_pictures/e03cf465-ab4e-45f2-8299-6619a1837dcd-profile_image-150x150.png",
    "Quantum075": "https://static-cdn.jtvnw.net/jtv_user_pictures/29e22097-499e-4b1b-a00b-153519fb95ba-profile_image-150x150.png",
}

STORY_DATA: dict[str, dict] = {
    "start": {
        "character_img": "/static/images/extension/bg_start.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Привет! Добро пожаловать на мой стрим~ Пока стример занят игрой, могу составить тебе компанию. Хочешь что-то узнать?",
        "choices": [
            {"text": "Давай познакомимся", "next_step": "quantum_intro"},
            {"text": "Хочу узнать о боте", "next_step": "about_bot"},
        ]
    },


    "quantum_intro": {
        "character_img": "/static/images/extension/bg_start.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "Меня зовут Квантум. Если удобнее, можешь звать меня Тоша. Я айтишник-программист. В свободное время иногда стримлю",
        "choices": [
            {"text": "Расскажи про стримы", "next_step": "about_streams"},
            {"text": "Расскажи про айти", "next_step": "about_it"},
        ],
    },
    "about_streams": {
        "character_img": "/static/images/extension/bg_stream.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "С радостью расскажу про стримы. Что именно хочешь узнать?",
        "choices": [
            {"text": "Как давно ты стримишь?", "next_step": "about_streams_how_long"},
            {"text": "Что ты обычно стримишь?", "next_step": "about_streams_what_stream"},
            {"text": "Когда обычно стримишь?", "next_step": "about_streams_when"},
        ],
    },
    "about_streams_when": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "Я стримлю вечерком после работы, если на это есть силы, либо на выходных. А так у меня нет конкретного расписания",
        "choices": [
            {"text": "А что ты обычно стримишь?", "next_step": "about_streams_what_stream"},
            {"text": "А как давно ты стримишь?", "next_step": "about_streams_how_long"},
        ],
    },
    "about_streams_how_long": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "Я начал стримить в 2022-ом году, ради любопытства, в целях практики английского и социальных навыков. Потом перешёл на русский и с 24-го года занимаюсь стримами уже более серьёзно.",
        "choices": [
            {"text": "А что ты обычно стримишь?", "next_step": "about_streams_what_stream"},
            {"text": "А когда обычно стримишь?", "next_step": "about_streams_when"},
        ],
    },
    "about_streams_what_stream": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "Раньше я стримил SAR. Потом частенько мелькала фазма. Сейчас чаще всего ДБД, но стараюсь разбавлять и различными другими играми.",
        "choices": [
            {"text": "А как давно ты стримишь?", "next_step": "about_streams_how_long"},
            {"text": "А когда обычно стримишь?", "next_step": "about_streams_when"},
            {"text": "Расскажи ещё про айти", "next_step": "about_it"},
        ],
    },
    "about_it": {
        "character_img": "/static/images/extension/bg_coding.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "Ну, я работаю Python Back-end разработчиком. Иногда разрабатываю что-то для себя, например вот эту панельку, на которую ты сейчас смотришь, или бота @Quantum075Bot",
        "choices": [
            {"text": "Где работаешь?", "next_step": "about_work"},
            {"text": "Расскажи про бота", "next_step": "about_bot"},
        ],
    },
    "about_work": {
        "character_img": "/static/images/extension/bg_coding.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "На своей первой работе я делал прошившки для устройств умного дома в Rubetek. Потом ушёл в Яндекс. После него попал на проект Пушкинская Карта в Нотамедиа, а оттуда как-то случайно оказался в ВК. Сейчас работаю в ВК на проекте ВК Билеты.",
        "choices": [
            {"text": "Верни стену", "next_step": "return_the_wall"},
            {"text": "Расскажи про бота", "next_step": "about_bot"},
        ],
    },
    "return_the_wall": {
        "character_img": "/static/images/extension/bg_moon.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "Нет! 🌚",
        "choices": [
            {"text": "Расскажи про бота", "next_step": "about_bot"},
        ],
    },


    "about_bot": {
        "character_img": "/static/images/extension/bg_bot_happy.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Привет! Я бот, @Quantum075 делает меня с июля 2025-го года. Изначально я создавался чисто для моего автора и его друзей, но сейчас мной пользуются уже довольно много стримеров, а я продолжаю обрастать полезными функциями! OwO",
        "choices": [
            {"text": "Что ты умеешь?", "next_step": "about_bot_functions"},
            {"text": "Кто тобой пользуется?", "next_step": "bot_users"},
        ],
    },
    "bot_users": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Мной пользуются уже %d человек! Вот некоторые из них: %s",
        "choices": [
            {"text": "Покажи всех", "link": "https://bot.quantum0.ru/streamers"},
            {"text": "А что ты умеешь?", "next_step": "about_bot_functions"},
        ],
    },
    "about_bot_functions": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Я умею выдавать мемкоины за баллы канала, могу быть чат-ботов и реагировать на команды в чате",
        "choices": [
            {"text": "Расскажи про мемкоины", "next_step": "about_memealerts"},
            {"text": "Расскажи про чат-бота", "next_step": "about_chat_bot"},
            {"text": "А что ещё умеешь?", "next_step": "about_bot_functions_2"},
        ],
    },
    "about_memealerts": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Есть замечательный сервис Memealerts, который позволяет кидать мемы на стрим. А я позволяю зрителям автоматически получать бесплатные мемкоины \"покупая\" их за баллы канала!",
        "choices": [
            {"text": "Интересно! А зачем?", "next_step": "about_memealerts_why"},
            {"text": "Круто! А как это подключить?", "next_step": "connect_bot"},
        ],
    },
    "about_memealerts_why": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Это один из способов потратить баллы, оно мотивирует людей сидеть на стриме, или как минимум луркать, а так же позволяет награжать постоянных зрителей!",
        "choices": [
            {"text": "...", "next_step": "about_memealerts_why_2"},
        ],
    },
    "about_memealerts_why_2": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "Раньше я частенько выдавал мемкоины бесплатно тем, кто часто заходит ко мне на стримы и активничает в чатике. Для меня цель - чтоб было интереснее смотреть стрим, а не чтоб собрать как можно больше донатов."
                "И я делал эту функцию, чтоб мои постоянные зрители могли получать мемкоины, не попрошайничая их, а мне не приходилось отвлекаться от стрима и заходить в панель управления Memealerts.",
        "choices": [
            {"text": "Круто! А как это подключить?", "next_step": "connect_bot"},
            {"text": "Звучит так себе", "next_step": "about_memealerts_dont_like"},
        ],
    },
    "about_memealerts_dont_like": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075",
        "avatar": PFP["Quantum075"],
        "text": "Ну что сказать, это ваше дело, я вас не заставляю ^w^",
        "choices": [
        ],
    },

    "about_chat_bot": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "В чате я умею реагировать на различные команды. Ответов много, они все довольно разнообразны, в некоторых я умею запоминать состояние и т.п.",
        "choices": [
            {"text": "А какие есть команды?", "next_step": "about_commands"},
            {"text": "Как это подключить?", "next_step": "connect_bot"},
        ],
    },
    "about_commands": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Есть команды для взаимодействия с другими, например !кусь !лизь !обнять !боньк; Есть команды которые отновятся только к себе, например !банан !якто; Моя любимая команда - !трусы =w=",
        "choices": [
            {"text": "Хочу попробовать!", "next_step": "try_command"},
            {"text": "Какие команды подключены у стримера?", "next_step": "streamer_commands"},
            {"text": "В чём именно они проработаны", "next_step": "about_commands_2"},
            {"text": "Хочу подключить!", "next_step": "connect_bot"},
        ],
    },
    "about_commands_2": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Ну, возьмём например !кусь. Ты можешь написать !кусь себя, !кусь стримера, !кусь кого-нибудь. Можешь указать цель через @. Можно указать несколько целей."
                "Если будешь часто кусаться, бот ответит тебе, что твои зупки устали кусаться и им нужно отдохнуть. Отдельно есть ответы на !кусь @Quantum075Bot и на !кусь другого бота."
                "На каждый из этих случаев есть несколько различных ответов. Попробуй!",
        "choices": [
            {"text": "Хочу попробовать!", "next_step": "try_command"},
            {"text": "Хочу подключить!", "next_step": "connect_bot"},
        ],
    },

    "about_bot_functions_2": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Я умею делать автоматический shoutout на рейды, у меня есть парочка интересных интерактивных оверлеев для развлечение зрителей, а ещё сейчас я делаю ИИ стикеры!",
        "choices": [
            {"text": "Какие оверлеи?", "next_step": "about_overlays"},
            # {"text": "Что за стикеры?", "next_step": "about_memealerts"},
            {"text": "Хочу подключить!", "next_step": "connect_bot"},
        ],
    },
    "about_overlays": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "У меня есть оверлей со звёздочкой, висящей на ниточке, которую зрители могут дёргать прям тыкая мышкой по стриму."
                "Так же на сайте бота можно подключить Словотрон, сделанный Фразой, как оверлей в OBS.",
        "choices": [
            {"text": "Хочу посмотреть и подключить!", "next_step": "connect_bot"},
        ],
    },

    "connect_bot": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Отлично, сейчас я направлю тебя на сайт, где ты сможешь подключить заинтересовавшие тебя функции на свой стрим! >w< Приятного пользования!!!",
        "choices": [
            {"text": "Let's gooooo", "link": "https://bot.quantum0.ru/"},
        ],
    },

    "try_command": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Выбирай команду — и я её выполню прямо в чате!",
        "choices": [],  # заполняется динамически по настройкам стримера
    },
    "try_command_done": {
        "character_img": "/static/images/extension/bg_bot_happy.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "Готово! Я отправил сообщение в чат — бот ответит в ближайшую секунду ^w^",
        "choices": [
            {"text": "Попробовать ещё!", "next_step": "try_command"},
            {"text": "Хочу подключить!", "next_step": "connect_bot"},
        ],
    },
    "try_command_unavailable": {
        "character_img": "/static/images/extension/bg_thinking.png",
        "speaker": "Quantum075Bot",
        "avatar": PFP["Quantum075Bot"],
        "text": "К сожалению, у этого стримера все интерактивные команды выключены. Если хочешь их у себя — подключи бота!",
        "choices": [
            {"text": "Хочу подключить!", "next_step": "connect_bot"},
            {"text": "Вернуться назад", "next_step": "about_commands"},
        ],
    },

    # streamer_commands: TODO сцена не реализована
}


ALLOWED_COMMANDS = ("кусь", "лизь", "банан")
TARGET_VARIANTS = ("кого-нибудь", "стримера", "@Quantum075Bot")
COMMAND_REGEX = re.compile(
    r"^!(?:кусь|лизь|банан) (?:кого-нибудь|стримера|@Quantum075Bot)$",
    re.IGNORECASE,
)


def verify_extension_token(authorization: str | None, channel_id: str | int) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(
            token,
            base64.b64decode(settings.extension_secret.get_secret_value()),
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token") from None

    if str(payload.get("channel_id")) != str(channel_id):
        raise HTTPException(status_code=403, detail="channel_id mismatch")

    return payload


def _try_commands_enabled(s: TwitchUserSettings | None) -> list[str]:
    if s is None:
        return []
    enabled = []
    if s.enable_bite:
        enabled.append("кусь")
    if s.enable_lick:
        enabled.append("лизь")
    if s.enable_banana:
        enabled.append("банан")
    return enabled


def _build_try_command_choices(enabled_commands: list[str]) -> list[dict]:
    return [
        {
            "text": f"!{cmd}",
            "action": "send_chat",
            "command": f"!{cmd} {random.choice(TARGET_VARIANTS)}",
            "next_step": "try_command_done",
        }
        for cmd in enabled_commands
    ]


async def _load_streamer_settings(db: AsyncSession, channel_id: str | int) -> TwitchUserSettings | None:
    # NOTE: можно закешировать результат в Redis по ключу ext_settings:{channel_id} на ~60с,
    # чтобы не лезть в БД на каждый запрос сцены about_commands/try_command.
    result = await db.execute(
        sa.select(TwitchUserSettings)
        .join(User, User.id == TwitchUserSettings.user_id)
        .where(User.twitch_id == str(channel_id))
    )
    return result.scalar_one_or_none()


async def _resolve_bot_users_data(db: AsyncSession, twitch: Twitch, cache: Cache) -> tuple[int, str]:
    total_count = (await db.execute(sa.select(sa.func.count()).select_from(User))).scalar() or 0

    q = (
        sa.select(
            User.login_name.label("username"),
            User.profile_image_url.label("avatar_url"),
            User.followers_count.label("followers"),
            User.in_beta_test.label("is_beta_tester"),
            User.donated.label("donated"),
            User.created_at.label("created_at"),
            User.interacted_at.label("interacted_at"),
            User.overlays_last_usage.label("overlays_last_usage"),
            TwitchUserSettings.enable_chat_bot.label("chat_bot_enabled"),
            TwitchUserSettings.enable_pants.label("pants_enabled"),
            TwitchUserSettings.ai_sticker_reward_id.is_not(None).label("ai_stickers_enabled"),
            MemealertsSettings.memealerts_reward.is_not(None).label("memealerts_enabled"),
        )
        .select_from(User)
        .join(TwitchUserSettings)
        .join(MemealertsSettings)
        .where(User.followers_count > 2)
        .limit(500)
    )
    res = [row._asdict() for row in (await db.execute(q)).all()]

    online_streams = await cache.get_set("online_streams")
    if not online_streams and res:
        streams = await twitch.get_streams([row["username"] for row in res])
        online_streams = {row["username"] for row in res if streams.get(row["username"])}
        await cache.set_set("online_streams", online_streams, ttl=300)
        logger.info("Online streamers list loaded from twitch (extension)")
    else:
        logger.info("Online streamers list loaded from cache (extension)")

    for row in res:
        row["is_live"] = row["username"] in online_streams
        row["score"] = compute_streamer_score(row)

    res = sorted(res, key=compute_streamer_score, reverse=True)
    top5 = [row["username"] for row in res[:5]]
    return total_count, ", ".join(top5)


@router.get("/scene/{channel_id}/{scene_key}")
async def get_scene(
    channel_id: int,
    scene_key: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
    authorization: str = Header(default=None),
):
    verify_extension_token(authorization, channel_id)

    scene = STORY_DATA.get(scene_key)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")

    scene = dict(scene)  # shallow copy, чтобы не мутировать STORY_DATA

    # Фильтрация кнопки "Хочу попробовать!" если ни одна команда не включена
    if scene_key in {"about_commands", "about_commands_2"}:
        settings_row = await _load_streamer_settings(db, channel_id)
        if not _try_commands_enabled(settings_row):
            scene["choices"] = [
                c for c in scene["choices"] if c.get("next_step") != "try_command"
            ]

    # Динамическая генерация кнопок для try_command
    if scene_key == "try_command":
        settings_row = await _load_streamer_settings(db, channel_id)
        enabled = _try_commands_enabled(settings_row)
        if not enabled:
            scene = dict(STORY_DATA["try_command_unavailable"])
        else:
            scene["choices"] = _build_try_command_choices(enabled)

    # Подстановка плейсхолдеров %d/%s в bot_users
    if scene_key == "bot_users":
        count, names = await _resolve_bot_users_data(db, twitch, cache)
        scene["text"] %= count, names

    return {"success": True, "scene": scene}


class SendChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=280)


@router.post("/chat/{channel_id}")
async def send_extension_chat(
    channel_id: int,
    body: SendChatRequest,
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
    authorization: str = Header(default=None),
):
    payload = verify_extension_token(authorization, channel_id)

    if not COMMAND_REGEX.match(body.text):
        raise HTTPException(
            status_code=400,
            detail="Message must be one of: '!кусь|!лизь|!банан кого-нибудь|стримера|@Quantum075Bot'",
        )

    if not await cache.check_rate_limit(f"ext_chat:{channel_id}", 12, 60):
        raise HTTPException(status_code=429, detail="Rate limit: 12 msgs/min/channel")

    resp = await twitch.send_extension_chat_message(
        broadcaster_id=str(channel_id),
        text=body.text,
        user_id=str(payload.get("user_id") or ""),
    )
    if resp.status_code == 204:
        return {"success": True}
    if resp.status_code == 400:
        raise HTTPException(status_code=400, detail=f"Twitch: {resp.text}")
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail=f"Twitch: {resp.text}")
    raise HTTPException(status_code=502, detail=f"Twitch returned {resp.status_code}: {resp.text}")
