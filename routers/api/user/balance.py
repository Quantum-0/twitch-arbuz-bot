from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BalanceOperation, User
from dependencies import get_db
from routers.security_helpers import user_auth
from schemas.api import BalanceHistoryResponseSchema, BalanceOperationSchema

router = APIRouter(prefix="/balance", tags=["Balance history"])


@router.get("/operations")
async def list_operations(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Security(user_auth)],
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> BalanceHistoryResponseSchema:
    """История начислений и списаний баланса текущего пользователя (новее → старше).

    Курсорная пагинация по ``id`` (identity монотонен): ``next_cursor`` — id
    последней строки страницы, передаётся в ``before_id`` для следующей.
    """
    stmt = sa.select(BalanceOperation).where(BalanceOperation.twitch_id == user.twitch_id)
    if before_id is not None:
        stmt = stmt.where(BalanceOperation.id < before_id)
    stmt = stmt.order_by(BalanceOperation.id.desc()).limit(limit + 1)

    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    return BalanceHistoryResponseSchema(
        items=[
            BalanceOperationSchema(
                id=op.id,
                created_at=op.created_at.isoformat(),
                type=op.type,
                action=op.action,
                amount=op.amount,
                balance_after=op.balance_after,
                details=op.details,
            )
            for op in rows
        ],
        next_cursor=rows[-1].id if has_more and rows else None,
    )
