from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import CharacterInfo, User
from dependencies import get_db
from routers.security_helpers import user_auth
from schemas.api import BoolResponseSchema
from utils.reference_moderation import ensure_reference_moderator

router = APIRouter(prefix="/moderation/references", tags=["Reference moderation"])


class ReferenceModerationRequest(BaseModel):
    approved: bool


@router.patch("/{reference_id}")
async def moderate_reference(
    reference_id: UUID,
    payload: ReferenceModerationRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Security(user_auth)],
) -> BoolResponseSchema:
    ensure_reference_moderator(user)
    result = await db.execute(
        sa.update(CharacterInfo)
        .where(CharacterInfo.id == reference_id)
        .values(approved=payload.approved)
        .returning(CharacterInfo.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Reference not found")
    await db.commit()
    return BoolResponseSchema(result=True)
