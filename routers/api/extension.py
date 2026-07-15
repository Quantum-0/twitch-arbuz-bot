import base64

import jwt
from fastapi import Header, HTTPException, APIRouter

from config import settings

router = APIRouter(prefix="/extension", tags=["Extension API"])

PFP = {
    "Quantum075Bot": "https://static-cdn.jtvnw.net/jtv_user_pictures/e03cf465-ab4e-45f2-8299-6619a1837dcd-profile_image-150x150.png",
    "Quantum075": "https://static-cdn.jtvnw.net/jtv_user_pictures/29e22097-499e-4b1b-a00b-153519fb95ba-profile_image-150x150.png",
}

STORY_DATA = {
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

    # streamer_commands, try_command
}


# Эндпоинт для получения конкретной сцены
@router.get("/scene/{channel_id}/{scene_key}")
async def get_scene(channel_id: int, scene_key: str, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    try:
        # Проверяем токен Твича для безопасности
        jwt.decode(token, base64.b64decode(settings.extension_secret.get_secret_value()), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Ищем сцену в нашей "базе данных"
    scene = STORY_DATA.get(scene_key)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")

    return {"success": True, "scene": scene}
