from fastapi import APIRouter, Security, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth, admin_auth

import sqlalchemy as sa

router = APIRouter(prefix="/admin", tags=["Admin API"])

@router.post("/add_to_beta_test")
async def update_settings(
    _: None = Security(admin_auth),
    twitch_login: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    q = sa.update(User).values(in_beta_test=True).where(User.login_name == twitch_login)
    res = await db.execute(q)
    await db.commit()
    return {"success": res.rowcount}