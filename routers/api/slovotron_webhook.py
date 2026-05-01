from typing import Annotated

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Response, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from container import Container
from database.models import User
from dependencies import get_db
from routers.schemas import SlovotronWebhookSchema
from services.mqtt import MQTTClient

router = APIRouter(prefix="", tags=["Slovotron Webhook"])


@router.post(
    "/slovotron-webhook",
    responses={
        200: {},
        204: {"description": "Успешно обработано"},
        403: {"description": "Некорректный секрет"},
        404: {"description": "Канал (channel) не найден"},
        424: {"description": "Невалидная схема данных (Failed Dependency)"},
        500: {"description": "Внутренняя ошибка сервера"}
    }
)
@inject
async def slovotron_webhook(
    payload: SlovotronWebhookSchema,
    mqtt: Annotated[MQTTClient, Depends(Provide[Container.mqtt])],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not payload.validate_secret():
        raise HTTPException(status_code=403, detail="Invalid secret")
    user = (await db.execute(
        sa.select(User).where(User.login_name == payload.channel)
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await mqtt.publish(f"slovotron/{payload.event}/{payload.channel}", payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
