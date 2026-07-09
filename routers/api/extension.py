import base64

import jwt
from fastapi import Header, HTTPException, APIRouter

from config import settings

router = APIRouter(prefix="/extension", tags=["Extension API"])

STORY_DATA = {
    "start": {
        "character_img": "/static/images/extension/bg_start.png",
        "speaker": "Quantum075Bot",
        "avatar": "https://static-cdn.jtvnw.net/jtv_user_pictures/e03cf465-ab4e-45f2-8299-6619a1837dcd-profile_image-150x150.png",
        "text": "Привет! Добро пожаловать на мой стрим. Чо хочешб?",
        "choices": [
            {"text": "Расскажи мне про стримера", "next_step": "about_author"},
            {"text": "Расскажи мне про бота", "next_step": "about_bot"}
        ]
    },
    "about_author": {
        "character_img": "/static/images/extension/bg_start.png",
        "speaker": "Quantum075",
        "avatar": "https://static-cdn.jtvnw.net/jtv_user_pictures/29e22097-499e-4b1b-a00b-153519fb95ba-profile_image-150x150.png",
        "text": "О, привет! Можешь звать меня Квантум (или Тоша, если так удобнее). Я программист и в свободное время иногда стримлю.",
        "choices": [
            {"text": "А чо погромируеш?", "next_step": "about_programming"}
        ],
    },
    "about_programming": {
        "character_img": "/static/images/extension/bg_coding.png",
        "speaker": "Quantum075",
        "avatar": "https://static-cdn.jtvnw.net/jtv_user_pictures/29e22097-499e-4b1b-a00b-153519fb95ba-profile_image-150x150.png",
        "text": "Ну, я работаю Python Back-end разработчиком. Иногда разрабатываю что-то для себя, например вот эту панельку, на которую ты сейчас смотришь, или бота @Quantum075Bot",
        "choices": [],
    },
    "about_bot": {
        "character_img": "/static/images/extension/bg_coding.png",
        "speaker": "Quantum075Bot",
        "avatar": "https://static-cdn.jtvnw.net/jtv_user_pictures/e03cf465-ab4e-45f2-8299-6619a1837dcd-profile_image-150x150.png",
        "text": "Я умею отвечать на команды в чатике, начислять мемкоины и бла-бла-бла",
        "choices": []
    }
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
