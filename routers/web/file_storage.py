from typing import Annotated
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Path, Depends, HTTPException, Query
from starlette.responses import Response

from container import Container
from schemas.enums import FileStorageDir
from services.s3 import FileStorage, FileNotExistError
from services.stickers import StickersService

router = APIRouter(prefix="/files", tags=["File Storage"])


@router.get("/{dir}/{file_id}")
@inject
async def get_file(
    dir: Annotated[FileStorageDir, Path(description="Целевая директория (раздел)")],
    file_id: Annotated[UUID, Path(description="Уникальный идентификатор сущности в формате UUID")],
    stickers: Annotated[StickersService, Depends(Provide[Container.stickers_service])],
    s3: Annotated[FileStorage, Depends(Provide[Container.s3])],
    mark_viewed: bool = Query(False),
):
    if dir == FileStorageDir.AI_GENERATED_STICKER:
        if file := await stickers.get_sticker(file_id, mark_viewed):
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
            return Response(content=file, media_type="image/png", headers=headers)
        raise HTTPException(404, "Object not found")
    if dir == FileStorageDir.REFS:
        try:
            data = await s3.get_object(f"{FileStorageDir.REFS}/{file_id}.png")
        except FileNotExistError:
            raise HTTPException(404, "Object not found")
        return Response(content=data, media_type="image/png")
    raise NotImplementedError
