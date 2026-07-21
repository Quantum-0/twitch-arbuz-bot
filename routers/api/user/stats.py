from datetime import datetime
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Security

from container import Container
from database.models import User
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
    user: User = Security(user_auth),
    type_: Annotated[StatsType, Query(alias="type", description="Тип метрики")] = StatsType.MESSAGE_INCOMING,
    subtype: Annotated[
        str | None,
        Query(description="Подтип метрики (для reward_*/command_handled). None — все подтипы вместе."),
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
    """
    points = await statistics.get_chart(
        type_,
        subtype=subtype,
        channel_id=None,
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
    user: User = Security(user_auth),
    type_: Annotated[
        StatsType,
        Query(alias="type", description="Тип метрики (обычно command_handled)"),
    ] = StatsType.COMMAND_HANDLED,
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
    series = await statistics.get_chart_series(
        type_,
        channel_id=None,
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


@router.get(
    "/users-count",
    response_model=StatsResponseSchema,
    responses={401: {"description": "Unauthorized"}},
)
@inject
async def get_users_count(
    statistics: Annotated[StatisticsService, Depends(Provide[Container.statistics])],
    user: User = Security(user_auth),
    period: Annotated[
        StatsPeriod,
        Query(description="Период агрегации (для users-count разрешены длинные диапазоны)"),
    ] = StatsPeriod.ONE_DAY,
    dt_from: Annotated[
        datetime | None,
        Query(
            alias="from",
            description="Начало диапазона (UTC). Не раньше 1 июля 2025 — принудительно.",
        ),
    ] = None,
    dt_to: Annotated[
        datetime | None,
        Query(alias="to", description="Конец диапазона (UTC). По умолчанию — now()."),
    ] = None,
) -> StatsResponseSchema:
    """Возвращает кумулятивный ряд числа пользователей бота по ``User.created_at``.

    Метрика не хранится в таблице ``statistics`` — считается на лету. Каждый
    бакет содержит суммарное число пользователей, зарегистрированных к началу
    этого бакета. Пустые бакеты заполняются предыдущим значением (cumulative
    carry-forward), поэтому график — монотонно-возрастающая линия.

    ``from`` принудительно не раньше 1 июля 2025. Диапазон ограничен в
    зависимости от ``period`` (см. ``USERS_COUNT_PERIOD_CONFIG``): для 10m —
    1 день, для 1d — 5 лет.
    """
    points = await statistics.get_users_count_chart(
        period=period,
        dt_from=dt_from,
        dt_to=dt_to,
    )
    return StatsResponseSchema(
        type="users_count",
        period=str(period),
        points=[StatsPointSchema(datetime=bucket, value=count) for bucket, count in points],
    )
