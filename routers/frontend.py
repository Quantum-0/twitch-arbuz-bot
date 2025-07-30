import random
from typing import Any

from fastapi import APIRouter, Security
from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa
from starlette.requests import Request
from starlette.responses import RedirectResponse, HTMLResponse
from starlette.templating import Jinja2Templates

from config import settings
from dependencies import get_twitch, get_db
from database.models import User
from routers.security_helpers import user_auth
from twitch.twitch import Twitch
from utils.memes import token_expires_in_days

templates = Jinja2Templates(directory="templates")

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/panel")
    else:
        return RedirectResponse(url="/login")

@router.get("/login")
async def login():
    # TODO: Добавить страничку с кнопочкой "авторизоваться через твич
    return RedirectResponse(settings.login_twitch_url)

@router.get("/panel")
async def main_page(
    request: Request,
    user: Any = Security(user_auth),
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
                "expires_in": await token_expires_in_days(user.memealerts.memealerts_token) if user.memealerts.memealerts_token else None,
                "coins_for_reward": user.memealerts.coins_for_reward
            },
        }
    )

@router.get("/about")
async def about_page(
    request: Request,
):
    return templates.TemplateResponse(
        "about.html",
        {
            "request": request,
        }
    )

@router.get("/memealerts-tutorial")
async def meme_tutorial_page(
    request: Request,
):
    return templates.TemplateResponse(
        "memealerts-tutorial.html",
        {
            "request": request,
        }
    )

@router.get("/streamers")
async def get_streamers(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    q = (
        sa.select(User.login_name.label("username"), User.profile_image_url.label("avatar_url"), User.followers_count.label("followers"), User.in_beta_test.label("is_beta_tester"))
        .where(User.followers_count > 20)
        .limit(50)
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
    return templates.TemplateResponse("streamers.html", {"request": request, "streamers": res})


@router.get("/login-callback")
async def callback(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_db),
    twitch: Twitch = Depends(get_twitch),
):
    access_token, refresh_token = await twitch.get_user_access_refresh_tokens_by_authorization_code(code)
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