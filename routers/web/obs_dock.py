from typing import Annotated
from uuid import uuid3, UUID

from fastapi import APIRouter, Query, Path, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.templating import Jinja2Templates

from config import settings
from database.models import User
from dependencies import get_db
import sqlalchemy as sa

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/dock", tags=["OBS dock panels"])


@router.get("/{channel_name}/slovotron")
async def slovotron_dock(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    secret: UUID = Query(),
    channel_name: str = Path(),
):
    if secret != uuid3(namespace=settings.slovotron_secret, name=channel_name):
        raise HTTPException(403, "Invalid secret")
    user: User = (await db.execute(  # type: ignore
        sa.select(User).where(User.login_name == channel_name)
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return templates.TemplateResponse(
        "docks/slovotron.html",
        {
            "request": request,
            "secret": secret,
            "user": user,
        },
    )