from collections.abc import Callable
from decimal import Decimal
from typing import Annotated

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Security
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from container import Container
from database.models import BalanceOperation, User
from dependencies import get_db
from routers.security_helpers import admin_auth, staff_auth, user_auth
from schemas.api import (
    AdminBalanceResponseSchema,
    AdminDepositRequestSchema,
    AdminDepositResponseSchema,
    HealthcheckResponseSchema,
)
from schemas.enums import BalanceOperationAction, BalanceOperationType
from services.healthcheck import run_healthchecks
from services.mqtt import MQTTClient
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch

router = APIRouter(prefix="/admin", tags=["Admin API"])

MAX_BALANCE = Decimal("1000.00")
MAX_DEPOSIT_PER_CALL = Decimal("500.00")


@router.get("/balance")
async def get_balance(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Security(admin_auth)],
    twitch_login: Annotated[str, Query(..., description="Twitch login стримера")],
) -> AdminBalanceResponseSchema:
    user = await db.scalar(sa.select(User).where(User.login_name == twitch_login))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return AdminBalanceResponseSchema(
        twitch_login=user.login_name,
        total_deposited=user.total_deposited,
        total_spent=user.total_spent,
        balance=user.balance,
    )


@router.post("/balance/deposit")
async def deposit_balance(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Security(admin_auth)],
    data: AdminDepositRequestSchema,
) -> AdminDepositResponseSchema:
    user = await db.scalar(sa.select(User).where(User.login_name == data.twitch_login).with_for_update())
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    deposited = min(data.amount, MAX_DEPOSIT_PER_CALL, MAX_BALANCE - user.balance)
    if deposited <= 0:
        return AdminDepositResponseSchema(twitch_login=user.login_name, deposited=Decimal("0.00"), balance=user.balance)

    # Читаем атрибуты ДО update: session.execute(update) экспайрит instance в
    # identity map, и повторное обращение к user.* стало бы синхронным
    # lazy-load'ом (MissingGreenlet в async).
    new_balance = user.balance + deposited
    user_twitch_id = user.twitch_id
    await db.execute(sa.update(User).values(total_deposited=User.total_deposited + deposited).where(User.id == user.id))
    db.add(
        BalanceOperation(
            twitch_id=user_twitch_id,
            type=BalanceOperationType.DEPOSIT,
            action=BalanceOperationAction.ADMIN_DEPOSIT,
            amount=deposited,
            balance_after=new_balance,
            details={"admin": settings.app.admin_api_login},
        )
    )
    await db.commit()
    await db.refresh(user)
    return AdminDepositResponseSchema(twitch_login=user.login_name, deposited=deposited, balance=user.balance)


@router.post("/add_to_beta_test")
async def update_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Security(admin_auth)],
    __: Annotated[None, Security(user_auth)],
    twitch_login: Annotated[str, Query(...)],
):
    q = sa.update(User).values(in_beta_test=True).where(User.login_name == twitch_login)
    res = await db.execute(q)
    await db.commit()
    return {"success": res.rowcount}  # type: ignore[attr-defined]


@router.post("/send_message")
@inject
async def send_message(
    db: Annotated[AsyncSession, Depends(get_db)],
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    _: Annotated[None, Security(admin_auth)],
    channel: Annotated[str, Query(...)],
    message: Annotated[str, Query(...)],
):
    q = sa.select(User).where(User.login_name == channel)
    res: User | None = await db.scalar(q)
    if not res:
        raise HTTPException(status_code=404, detail="User not found")

    await chat_bot.send_message(res, message)


@router.get("/healthcheck")
@inject
async def healthcheck(
    db_session_factory: Annotated[Callable[[], AsyncSession], Depends(Provide[Container.db_session_factory])],
    redis: Annotated[Redis, Depends(Provide[Container.redis])],
    mqtt: Annotated[MQTTClient, Depends(Provide[Container.mqtt])],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    _: Annotated[User, Security(staff_auth)],
) -> HealthcheckResponseSchema:
    """Статус связанных сервисов (DB / Redis / MQTT / TTS / Telegram / Twitch).

    Для status card на странице /server. Доступ — модераторам/админам (staff_auth,
    сессионная авторизация, как у самой страницы; остальные ручки /api/admin
    используют HTTP Basic и остаются для внешних скриптов).
    """
    return await run_healthchecks(
        db_session_factory=db_session_factory,
        redis=redis,
        mqtt=mqtt,
        twitch=twitch,
    )


@router.get("/eventsub/subscriptions")
@inject
async def get_eventsub_subscriptions(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    _: Annotated[None, Security(admin_auth)],
) -> dict:
    """Получить все EventSub-подписки сервиса с их cost.

    Возвращает список всех подписок (id, type, status, condition, cost, created_at)
    и сводку: total_count, total_cost, cost_by_type.
    """
    subscriptions = await twitch.get_subscriptions()

    items = [
        {
            "id": sub.id,
            "type": sub.type,
            "version": sub.version,
            "status": sub.status,
            "condition": sub.condition,
            "cost": sub.cost,
            "created_at": sub.created_at.isoformat() if sub.created_at else None,
        }
        for sub in subscriptions
    ]

    cost_by_type: dict[str, int] = {}
    for sub in subscriptions:
        cost_by_type[sub.type] = cost_by_type.get(sub.type, 0) + sub.cost

    return {
        "total_count": len(subscriptions),
        "total_cost": sum(sub.cost for sub in subscriptions),
        "cost_by_type": cost_by_type,
        "subscriptions": items,
    }
