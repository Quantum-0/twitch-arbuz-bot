from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth
from utils.stickers_query import build_public_stickers_query, serialize_sticker_rows

router = APIRouter(prefix="/galleries", tags=["Public galleries"])


@router.get("/stickers")
async def get_public_stickers(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Security(user_auth)],
    before: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=30)] = 20,
):
    before_dt: datetime | None = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError as exc:
            raise HTTPException(400, "Неверный формат даты для параметра 'before'. Ожидается ISO формат.") from exc

    rows = (await db.execute(build_public_stickers_query(before=before_dt, limit=limit))).all()
    return serialize_sticker_rows(rows, limit)
