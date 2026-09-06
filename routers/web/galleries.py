from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.templating import Jinja2Templates

from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth
from utils.galleries import build_public_references_query
from utils.template_globals import register_template_globals

templates = Jinja2Templates(directory="templates")
register_template_globals(templates)

router = APIRouter(tags=["Public galleries"])


@router.get("/stickers", response_class=HTMLResponse)
async def public_stickers_page(
    request: Request,
    user: Annotated[User, Security(user_auth)],
):
    return templates.TemplateResponse("stickers.html", {"request": request, "user": user})


@router.get("/references", response_class=HTMLResponse)
async def public_references_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Security(user_auth)],
):
    references = (await db.execute(build_public_references_query())).all()
    return templates.TemplateResponse(
        "references.html",
        {"request": request, "user": user, "references": references},
    )
