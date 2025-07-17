import logging

import httpx
import uvicorn
from fastapi import FastAPI, Request, Depends
from fastapi.params import Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from twitchAPI.types import TwitchAPIException

from chat_bot.bot import ChatBot
from config import settings
from database import get_db
from models import User
from twitch.twitch import Twitch

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="some-secret-key")
templates = Jinja2Templates(directory="templates")

logger = logging.getLogger()

@app.on_event("startup")
async def startup_event():
    await (Twitch().startup())
    await ChatBot().startup()
    await ChatBot().update_bot_channels()

@app.get("/login")
async def login():
    return RedirectResponse(settings.login_url)


@app.get("/callback")
async def callback(request: Request, code: str, db: Session = Depends(get_db)):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": settings.twitch_client_id,
                "client_secret": settings.twitch_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.login_redirect_url,
            }
        )
        tokens = response.json()
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

    from twitchAPI.twitch import Twitch
    user_twitch = Twitch(settings.twitch_client_id, settings.twitch_client_secret)
    await user_twitch.set_user_authentication(access_token, [], refresh_token)
    users_info = []
    async for usr in user_twitch.get_users():
        users_info.append(usr)
    user_info = users_info[0]
    user_id = user_info.id
    login_name = user_info.login
    profile_image_url = user_info.profile_image_url

    user = db.query(User).filter_by(twitch_id=user_id).first()
    if not user:
        user = User(
            twitch_id=user_id,
            login_name=login_name,
            profile_image_url=profile_image_url,
            access_token=access_token,
            refresh_token=refresh_token
        )
        db.add(user)
    else:
        user.access_token = access_token
        user.refresh_token = refresh_token
        user.profile_image_url = profile_image_url
    db.commit()

    request.session["user_id"] = user_id
    return RedirectResponse(url="/")

@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login")
    user = db.query(User).filter_by(twitch_id=user_id).first()
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "user": user}
    )

@app.post("/update_settings")
async def update_settings(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login")
    form = await request.form()
    enable_help = "enable_help" in form
    enable_random = "enable_random" in form
    enable_fruit = "enable_fruit" in form

    user = db.query(User).filter_by(twitch_id=user_id).first()
    if user:
        user.enable_help = enable_help
        user.enable_random = enable_random
        user.enable_fruit = enable_fruit
        db.commit()
        await ChatBot().update_bot_channels()
    return RedirectResponse(url="/", status_code=303)


@app.post("/memealerts")
async def update_settings(
    request: Request,
    enable: bool = Query(...),
    db: Session = Depends(get_db)
):
    # TODO: authorization
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login")
    user = db.query(User).filter_by(twitch_id=user_id).first()
    if enable:
        try:
            reward = await Twitch().create_reward(user)
        except TwitchAPIException as exc:
            pass
        await Twitch().subscribe_reward(user, reward.id)

        # TODO: если нету - создаём и пишем в бд
        # подписываемся

    # curl -X POST '' \
    # -H 'Authorization: Bearer 2gbdx6oar67tqtcmt49t3wpcgycthx' \
    # -H 'Client-Id: wbmytr93xzw8zbg0p1izqyzzc5mbiz' \
    # -H 'Content-Type: application/json' \
    # -d '{"type":"channel.follow","version":"2","condition":{"broadcaster_user_id":"1234", "moderator_user_id": "1234"},"transport":{"method":"webhook","callback":"https://example.com/callback","secret":"s3cre77890ab"}}'

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level="debug",
        reload=True,
    )