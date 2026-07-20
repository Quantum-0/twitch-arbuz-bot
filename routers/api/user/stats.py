from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession

from container import Container
from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth
from schemas.api import (
    StatsPeriod,
    StatsPointSchema,
    StatsResponseSchema,
    StatsSeriesItemSchema,
    StatsSeriesPointSchema,
    StatsSeriesResponseSchema,
    StatsType,
)
from services.statistics import StatisticsService

router = APIRouter(prefix="/stats", tags=["Monitoring stats"])


@router.get(
    "",
    response_model=StatsResponseSchema,
    responses={401: {"description": "Unauthorized"}},
)
@inject
async def get_stats(
    statistics: Annotated[StatisticsService, Depends(Provide[Container.statistics])],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User = Security(user_auth),
    type_: Annotated[StatsType, Query(alias="type", description="Тип метрики")] = StatsType.MESSAGE_INCOMING,
    subtype: Annotated[
        str | None,
        Query(description="Подтип метрики (для reward_*/command_handled). None — все подтипы вместе."),
    ] = None,
    channel: Annotated[
        str | None,
        Query(
            description=("Логин канала для фильтра. MVP не пишет per-channel данные — пока фильтр вернёт пустой ряд."),
        ),
    ] = None,
    period: Annotated[StatsPeriod, Query(description="Период агрегации")] = StatsPeriod.TEN_MIN,
    dt_from: Annotated[
        datetime | None,
        Query(
            alias="from",
            description=(
                "Начало диапазона (UTC). Если не задано — берётся максимально допустимый для period интервал от dt_to."
            ),
        ),
    ] = None,
    dt_to: Annotated[
        datetime | None,
        Query(alias="to", description="Конец диапазона (UTC). Если не задано — now()."),
    ] = None,
) -> StatsResponseSchema:
    """Возвращает ряд точек (UTC) для отрисовки графика Chart.js.

    Диапазон ``from``/``to`` жёстко ограничен в зависимости от ``period``
    (см. ``PERIOD_CONFIG`` в ``services/statistics.py``): если превышен —
    ``from`` сдвигается вперёд. Пустые бакеты внутри диапазона заполняются
    нулями, чтобы график был непрерывным.

    Параметр ``channel`` зарезервирован на будущее (когда появится per-channel
    сбор статистики); в текущей MVP-реализации все инкременты пишутся с
    ``channel_id=NULL`` (тотал по сервису), поэтому фильтр по каналу вернёт
    пустой ряд.
    """
    channel_id = await _resolve_channel_id(db, channel)

    points = await statistics.get_chart(
        type_,
        subtype=subtype,
        channel_id=channel_id,
        period=period,
        dt_from=dt_from,
        dt_to=dt_to,
    )
    return StatsResponseSchema(
        type=str(type_),
        subtype=subtype,
        period=str(period),
        points=[StatsPointSchema(datetime=bucket, value=count) for bucket, count in points],
    )


@router.get(
    "/series",
    response_model=StatsSeriesResponseSchema,
    responses={401: {"description": "Unauthorized"}},
)
@inject
async def get_stats_series(
    statistics: Annotated[StatisticsService, Depends(Provide[Container.statistics])],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: User = Security(user_auth),
    type_: Annotated[
        StatsType,
        Query(alias="type", description="Тип метрики (обычно command_handled)"),
    ] = StatsType.COMMAND_HANDLED,
    channel: Annotated[
        str | None,
        Query(description="Логин канала для фильтра (на будущее, MVP — total)."),
    ] = None,
    period: Annotated[StatsPeriod, Query(description="Период агрегации")] = StatsPeriod.TEN_MIN,
    top: Annotated[
        int,
        Query(description="Сколько топ-подтипов вернуть (по убыванию sum(count)).", ge=1, le=50),
    ] = 10,
    dt_from: Annotated[
        datetime | None,
        Query(alias="from", description="Начало диапазона (UTC)."),
    ] = None,
    dt_to: Annotated[
        datetime | None,
        Query(alias="to", description="Конец диапазона (UTC)."),
    ] = None,
) -> StatsSeriesResponseSchema:
    """Возвращает топ-N подтипов с рядами точек для multi-line графика.

    Один запрос к БД определяет топ-N подтипов (по сумме ``count`` за диапазон),
    второй — точки для каждого из них с ``date_bin`` агрегацией. Пустые бакеты
    заполняются нулями (zero-fill), чтобы все ряды имели одинаковую длину.
    """
    channel_id = await _resolve_channel_id(db, channel)

    series = await statistics.get_chart_series(
        type_,
        channel_id=channel_id,
        period=period,
        dt_from=dt_from,
        dt_to=dt_to,
        top_n=top,
    )
    return StatsSeriesResponseSchema(
        type=str(type_),
        period=str(period),
        series=[
            StatsSeriesItemSchema(
                subtype=subtype,
                points=[StatsSeriesPointSchema(datetime=bucket, value=value) for bucket, value in points],
            )
            for subtype, points in series
        ],
    )


async def _resolve_channel_id(db: AsyncSession, channel: str | None) -> int | None:
    """Логин канала → twitch_id, для per-channel фильтра (пока возвращает None для MVP)."""
    if channel is None:
        return None
    channel_login = channel.lower()
    twitch_id = await db.scalar(sa.select(User.twitch_id).where(User.login_name == channel_login))
    if twitch_id is None:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_login}' not found")
    return int(twitch_id)
