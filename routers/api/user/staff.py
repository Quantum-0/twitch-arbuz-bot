"""Staff-only API: метрики хоста для страницы «Сервер» и бейджи меню.

Доступ — только модераторам/админу (``utils.staff.ensure_staff``): нагрузка
хоста и очередь модерации не подлежат публичному показу.

Сервис метрик берётся через ``get_container()`` (конвенция AGENTS.md для
не-endpoint зависимостей), а не ``Provide[...]`` — так зависимость можно
переопределить в тестах через ``app.dependency_overrides``.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession

from container_runtime import get_container
from database.models import CharacterInfo, ServerMetric, User
from dependencies import get_db
from routers.security_helpers import user_auth
from schemas.api import (
    ServerAlertSchema,
    ServerMetricPointSchema,
    ServerMetricsPeriod,
    ServerMetricsResponseSchema,
    StaffBadgesSchema,
)
from services.server_metrics import Alert, ServerMetricsService
from utils.staff import ensure_staff

router = APIRouter(prefix="", tags=["Staff"])

# Диапазон выборки по period (retention метрик — 30 дней).
PERIOD_DELTA: dict[ServerMetricsPeriod, timedelta] = {
    ServerMetricsPeriod.ONE_DAY: timedelta(days=1),
    ServerMetricsPeriod.THREE_DAYS: timedelta(days=3),
    ServerMetricsPeriod.SEVEN_DAYS: timedelta(days=7),
    ServerMetricsPeriod.THIRTY_DAYS: timedelta(days=30),
}


def get_server_metrics_service(
    user: Annotated[User, Security(user_auth)],  # noqa: ARG001 — гейт авторизации
) -> ServerMetricsService:
    """DI-провайдер сервиса метрик хоста (переопределяется в тестах).

    Зависит от ``user_auth``, чтобы 401 неавторизованному отдавался до попытки
    разрешить контейнер (иначе в окружении без контейнера ответ был бы 500).
    """
    return get_container().server_metrics()


def _point(row: ServerMetric) -> ServerMetricPointSchema:
    return ServerMetricPointSchema(
        datetime=row.bucket_ts,
        cpu_avg=row.cpu_avg,
        cpu_max=row.cpu_max,
        load1_avg=row.load1_avg,
        load5_avg=row.load5_avg,
        load15_avg=row.load15_avg,
        load5_max=row.load5_max,
        mem_used_percent=row.mem_used_percent,
        disk_used_percent=row.disk_used_percent,
        disk_free_gb=row.disk_free_gb,
        net_rx_mb=row.net_rx_mb,
        net_tx_mb=row.net_tx_mb,
    )


def _alert_schema(alert: Alert) -> ServerAlertSchema:
    return ServerAlertSchema(metric=alert.metric, value=alert.value, limit=alert.limit, severity=alert.severity)


@router.get(
    "/server-metrics",
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden (не модератор)"},
    },
)
async def get_server_metrics(
    metrics: Annotated[ServerMetricsService, Depends(get_server_metrics_service)],
    user: Annotated[User, Security(user_auth)],
    period: Annotated[
        ServerMetricsPeriod,
        Query(description="Диапазон выборки, если не заданы from/to"),
    ] = ServerMetricsPeriod.SEVEN_DAYS,
    dt_from: Annotated[
        datetime | None,
        Query(alias="from", description="Начало диапазона (UTC). По умолчанию now() − period."),
    ] = None,
    dt_to: Annotated[
        datetime | None,
        Query(alias="to", description="Конец диапазона (UTC). По умолчанию now()."),
    ] = None,
) -> ServerMetricsResponseSchema:
    """Серия 6-часовых агрегатов нагрузки хоста + текущие алерты. Только штат."""
    ensure_staff(user)
    dt_to = dt_to or datetime.now(UTC)
    dt_from = dt_from or dt_to - PERIOD_DELTA[period]
    rows = await metrics.get_chart(dt_from, dt_to)
    alerts = await metrics.get_active_alerts()
    return ServerMetricsResponseSchema(
        points=[_point(row) for row in rows],
        alerts=[_alert_schema(alert) for alert in alerts],
    )


@router.get(
    "/staff-badges",
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden (не модератор)"},
    },
)
async def get_staff_badges(
    db: Annotated[AsyncSession, Depends(get_db)],
    metrics: Annotated[ServerMetricsService, Depends(get_server_metrics_service)],
    user: Annotated[User, Security(user_auth)],
) -> StaffBadgesSchema:
    """Счётчики для мигающих значков меню: алерты хоста + непроверенные референсы."""
    ensure_staff(user)
    alerts = await metrics.get_active_alerts()
    pending = (
        await db.execute(sa.select(sa.func.count()).select_from(CharacterInfo).where(CharacterInfo.approved.is_(None)))
    ).scalar_one()
    return StaffBadgesSchema(server_alerts=len(alerts), pending_references=int(pending))
