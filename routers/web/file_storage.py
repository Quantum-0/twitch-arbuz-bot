from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from botocore import exceptions as s3exc
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Path, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from container import Container
from database.models import GeneratedImage
from dependencies import get_db
from schemas.enums import FileStorageDir
from services.s3 import FileStorage

router = APIRouter(prefix="/files", tags=["File Storage"])


@router.get("/{dir}/{file_id}")
@inject
async def overlay_jumping_chibi(
    dir: Annotated[FileStorageDir, Path(description="Целевая директория (раздел)")],
    file_id: Annotated[UUID, Path(description="Уникальный идентификатор сущности в формате UUID")],
    s3: Annotated[FileStorage, Depends(Provide[Container.s3])],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    q_update_shown_at = (
        sa.update(GeneratedImage)
        .values(shown_at=sa.func.now())
        .where(GeneratedImage.file_id == file_id)
        .returning(GeneratedImage)
    )
    try:
        if dir == FileStorageDir.AI_GENERATED_STICKER:
            file = await s3.get_object(f"{dir}/{file_id}.png")
            result = (await db.execute(q_update_shown_at)).scalar_one_or_none()
            await db.commit()
            return Response(content=file, media_type="image/png")
        return Response(content=await s3.get_object(f"{dir}/{file_id}"))
    except s3exc.ClientError:
        result = (await db.execute(q_update_shown_at)).scalar_one_or_none()
        await db.commit()
        raise HTTPException(404, "Object not found")
