from datetime import datetime
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from database.models import User
from dependencies import get_db
from utils.stickers_query import build_stickers_query, serialize_sticker_rows

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/{login}/ai-stickers")
async def get_profile_ai_stickers(
    login: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    mode: Literal["mine", "with_me", "from_me"] = Query(default="mine"),
    before: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=30),
):
    profile_user = (
        await db.execute(sa.select(User).options(joinedload(User.settings)).filter_by(login_name=login))
    ).scalar_one_or_none()
    if profile_user is None:
        raise HTTPException(404, "Профиль не найден")

    if not profile_user.settings.ai_stickers_show_in_profile:
        raise HTTPException(403, "Стикеры скрыты владельцем профиля")

    before_dt: datetime | None = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(400, "Неверный формат даты для параметра 'before'. Ожидается ISO формат.")

    q = build_stickers_query(mode, int(profile_user.twitch_id), profile_user.login_name, before=before_dt, limit=limit)
    rows = (await db.execute(q)).all()
    return serialize_sticker_rows(rows, limit)
