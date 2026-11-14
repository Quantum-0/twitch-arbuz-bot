import logging
from collections.abc import Callable
from typing import Annotated

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from twitchAPI.type import TwitchAPIException, TwitchResourceNotFound

from container import Container
from database.models import MemealertsDeposit, MemealertsSettings, User
from dependencies import get_db
from routers.security_helpers import user_auth
from schemas.api import (
    MemealertsDepositSchema,
    MemealertsDepositsResponseSchema,
    UpdateMemealertsCurrencySchema,
    UpdateMemealertsRewardSchema,
)
from services.memes_v2 import MemealertsOAuthService
from services.twitch_token_service import TwitchTokenExpiredError
from twitch.client.twitch import Twitch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memealerts", tags=["Memealerts"])


@router.delete("", response_class=JSONResponse)
@inject
async def delete_ma_tokens(
    memealerts: Annotated[MemealertsOAuthService, Depends(Provide[Container.memealerts_auth])],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: Annotated[User, Security(user_auth)],
):
    await memealerts.delete_token(user)
    # Отключаем награду на Twitch, чтобы зрители не могли её использовать,
    # пока интеграция с Memealerts отключена. Награда не удаляется —
    # при повторном подключении она будет включена обратно.
    if user.memealerts.memealerts_reward:
        try:
            await twitch.disable_reward(user, user.memealerts.memealerts_reward)
        except Exception:
            logger.warning("Не удалось отключить награду при отвязке Memealerts", exc_info=True)
    return JSONResponse(
        content={
            "title": "Memealerts",
            "message": "Привязка к аккаунту Memealerts удалена.",
        },
        status_code=200,
    )


@router.delete("/reward", response_class=JSONResponse)
@inject
async def disable_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: Annotated[User, Security(user_auth)],
):
    if not user.memealerts.memealerts_reward:
        return JSONResponse(
            content={
                "title": "Ошибка",
                "message": "Награда не найдена.",
            },
            status_code=404,
        )
    try:
        await twitch.disable_reward(user, user.memealerts.memealerts_reward)
    except TwitchTokenExpiredError:
        return JSONResponse(
            {"title": "Ошибка", "message": "Токен Twitch недействителен. Выйдите из аккаунта и войдите снова."},
            400,
        )
    return JSONResponse(
        content={
            "title": "Memealerts",
            "message": "Награда для начисления мемкоинов отключена.",
        },
        status_code=200,
    )


@router.put("/reward", response_class=JSONResponse, status_code=201)
@inject
async def create_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    db_session_factory: Annotated[Callable[[], AsyncSession], Depends(Provide[Container.db_session_factory])],
    user: Annotated[User, Security(user_auth)],
):
    try:
        reward = await twitch.create_reward(
            user,
            f"Получить {(user.memealerts.memecoin_name_accusative_multiple or 'мемкоины').lower()}",
            1000,
            "Награда начисляется автомагически. В комментарии к награде обязательно укажи свой полный ник или ID на Memealerts. ОБЯЗАТЕЛЬНО заберите приветственный бонус.",
            is_user_input_required=True,
        )
    except TwitchTokenExpiredError:
        return JSONResponse(
            {"title": "Ошибка", "message": "Токен Twitch недействителен. Выйдите из аккаунта и войдите снова."},
            400,
        )
    except TwitchAPIException as exc:
        if "CREATE_CUSTOM_REWARD_DUPLICATE_REWARD" in str(exc):
            return JSONResponse({"title": "Ошибка", "message": "Награда уже существует."}, 400)
        if "CREATE_CUSTOM_REWARD_TOO_MANY_REWARDS" in str(exc):
            return JSONResponse(
                {"title": "Ошибка", "message": "Слишком много наград на канале."},
                400,
            )
        return JSONResponse(
            {"title": "Ошибка", "message": str(exc)},
            400,
        )

    async with db_session_factory() as db:
        await db.execute(
            sa.update(MemealertsSettings)
            .where(MemealertsSettings.user_id == user.id)
            .values(memealerts_reward=reward.id)
        )
        await db.commit()
    await twitch.subscribe_reward(user, reward.id)
    return JSONResponse({"title": "Успешно", "message": "Награда создана."}, 201)


@router.get("/reward", response_class=JSONResponse)
@inject
async def get_reward_cost(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: Annotated[User, Security(user_auth)],
):
    """Текущая стоимость награды в баллах канала (для панели)."""
    if not user.memealerts.memealerts_reward:
        return JSONResponse({"title": "Ошибка", "message": "Награда не существует."}, 400)
    try:
        reward = await twitch.get_reward(user, user.memealerts.memealerts_reward)
    except TwitchTokenExpiredError:
        return JSONResponse(
            {"title": "Ошибка", "message": "Токен Twitch недействителен. Выйдите из аккаунта и войдите снова."},
            400,
        )
    except TwitchAPIException as exc:
        return JSONResponse({"title": "Ошибка", "message": str(exc)}, 400)
    return JSONResponse({"cost": reward.cost if reward else None})


@router.patch("/reward", response_class=JSONResponse)
@inject
async def update_reward(
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    user: Annotated[User, Security(user_auth)],
    payload: UpdateMemealertsRewardSchema | None = None,
):
    if not user.memealerts.memealerts_reward:
        return JSONResponse({"title": "Ошибка", "message": "Награда не существует."}, 400)
    try:
        await twitch.update_reward(
            user,
            user.memealerts.memealerts_reward,
            is_enabled=True,
            is_user_input_required=True,
            should_redemptions_skip_request_queue=False,
            cost=payload.cost if payload else None,
        )
    except TwitchTokenExpiredError:
        return JSONResponse(
            {"title": "Ошибка", "message": "Токен Twitch недействителен. Выйдите из аккаунта и войдите снова."},
            400,
        )
    except TwitchResourceNotFound:
        return JSONResponse(
            {"title": "Ошибка", "message": "Награда не найдена на Twitch — создайте её заново."},
            400,
        )
    except TwitchAPIException as exc:
        return JSONResponse({"title": "Ошибка", "message": str(exc)}, 400)
    message = "Стоимость награды обновлена." if payload and payload.cost is not None else "Награда обновлена."
    return JSONResponse({"title": "Успешно", "message": message}, 200)


@router.patch("/currency", response_class=JSONResponse)
async def update_currency(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Security(user_auth)],
    payload: UpdateMemealertsCurrencySchema,
):
    """Пользовательские склонения названия валюты (override над значениями Memealerts).

    Непустая строка — override (заголовок награды и сообщения в чат используют
    его); пустая строка — сброс поля к автоматическому значению Memealerts.
    """
    values = {
        "memecoin_name_genitive_override": payload.genitive,
        "memecoin_name_accusative_override": payload.accusative,
        "memecoin_name_genitive_multiple_override": payload.genitive_multiple,
        "memecoin_name_accusative_multiple_override": payload.accusative_multiple,
    }
    overrides = {column: (value.strip() or None) for column, value in values.items() if value is not None}
    if overrides:
        await db.execute(sa.update(MemealertsSettings).where(MemealertsSettings.user_id == user.id).values(**overrides))
        await db.commit()
    return JSONResponse({"title": "Memealerts", "message": "Название валюты обновлено."}, 200)


@router.get("/deposits")
async def list_deposits(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Security(user_auth)],
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> MemealertsDepositsResponseSchema:
    """История покупок мемкоинов через награду текущего пользователя (новее → старше).

    Курсорная пагинация по ``id`` (identity монотонен): ``next_cursor`` — id
    последней строки страницы, передаётся в ``before_id`` для следующей.
    """
    stmt = sa.select(MemealertsDeposit).where(MemealertsDeposit.twitch_id == user.twitch_id)
    if before_id is not None:
        stmt = stmt.where(MemealertsDeposit.id < before_id)
    stmt = stmt.order_by(MemealertsDeposit.id.desc()).limit(limit + 1)

    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    return MemealertsDepositsResponseSchema(
        items=[
            MemealertsDepositSchema(
                id=row.id,
                created_at=row.created_at,
                chatter_login=row.chatter_login,
                memealerts_username=row.memealerts_username,
                points_spent=row.points_spent,
                memecoins=row.memecoins,
                status=row.status,
                error=row.error,
            )
            for row in rows
        ],
        next_cursor=rows[-1].id if has_more and rows else None,
    )
