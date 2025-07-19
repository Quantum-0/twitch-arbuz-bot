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
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "settings": user.settings,
            "memealerts": {
                "enabled": user.memealerts.memealerts_reward is not None,
                "expires_in": token_expires_in_days(user.memealerts.memealerts_token) if user.memealerts.memealerts_token else None,
            },
        }
    )

@router.get("/about")
async def about_page(
    request: Request,
    user: Any = Security(user_auth),
):
    return templates.TemplateResponse(
        "about.html",
        {
            "request": request,
        }
    )

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
    await db.commit()

    request.session["user_id"] = user_id
    return RedirectResponse(url="/panel")