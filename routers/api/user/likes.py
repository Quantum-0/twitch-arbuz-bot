from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Security, status
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User, UserLike
from dependencies import get_db
from routers.security_helpers import user_auth

router = APIRouter(prefix="/likes", tags=["User likes"])


async def _get_target(db: AsyncSession, target_user_id: int) -> User:
    target = await db.get(User, target_user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return target


@router.put("/{target_user_id}")
async def like_user(
    target_user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Security(user_auth)],
) -> dict[str, int | bool]:
    if user.id == target_user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot like yourself")
    await _get_target(db, target_user_id)
    stmt = (
        insert(UserLike)
        .values(from_user_id=user.id, to_user_id=target_user_id)
        .on_conflict_do_nothing(index_elements=[UserLike.from_user_id, UserLike.to_user_id])
    )
    await db.execute(stmt)
    await db.commit()
    likes_count = await db.scalar(
        sa.select(sa.func.count(UserLike.id)).where(UserLike.to_user_id == target_user_id)
    )
    return {"liked": True, "likes_count": likes_count or 0}


@router.delete("/{target_user_id}")
async def unlike_user(
    target_user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Security(user_auth)],
) -> dict[str, int | bool]:
    if user.id == target_user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot like yourself")
    await _get_target(db, target_user_id)
    await db.execute(
        sa.delete(UserLike).where(
            UserLike.from_user_id == user.id,
            UserLike.to_user_id == target_user_id,
        )
    )
    await db.commit()
    likes_count = await db.scalar(
        sa.select(sa.func.count(UserLike.id)).where(UserLike.to_user_id == target_user_id)
    )
    return {"liked": False, "likes_count": likes_count or 0}
