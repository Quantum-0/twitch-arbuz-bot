# ruff: noqa: S101, ARG001

"""Роут-тесты страницы «Сервер» и staff-API: доступ только модераторам."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from database.models import CharacterInfo
from main import app
from routers.api.user.staff import get_server_metrics_service
from services.server_metrics import LAST_KEY, HostSample, ServerMetricsService, _sample_to_json
from tests.unit.fixtures.fake_redis import FakeRedis, session_factory

MODERATOR_LOGIN = "fufigg"


def _host_sample(disk: float) -> HostSample:
    return HostSample(
        ts=datetime.now(UTC),
        cpu_percent=10.0,
        load1=0.5,
        load5=0.7,
        load15=0.9,
        mem_used_percent=55.0,
        disk_used_percent=disk,
        disk_free_gb=12.0,
        net_rx_bytes=1000.0,
        net_tx_bytes=500.0,
    )


@pytest.fixture
def metrics_service(db_session):
    """Сервис метрик на FakeRedis + тестовой сессии; live-сэмпл с критичным диском."""
    service = ServerMetricsService(session_factory(db_session))
    fake = FakeRedis()
    fake.values[LAST_KEY] = _sample_to_json(_host_sample(disk=95.0))
    service._r = fake  # noqa: SLF001 — подмена redis без startup()
    return service


@pytest.fixture
def metrics_override(metrics_service):
    app.dependency_overrides[get_server_metrics_service] = lambda: metrics_service
    yield metrics_service
    app.dependency_overrides.pop(get_server_metrics_service, None)


# ----------------------------------------------------------------------
# Страница /server
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_page_requires_login(client):
    resp = await client.get("/server")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_server_page_forbidden_for_regular_user(client, user_auth_mock):
    resp = await client.get("/server")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_server_page_ok_for_moderator(client, user_auth_mock, test_user):
    test_user.login_name = MODERATOR_LOGIN
    resp = await client.get("/server")
    assert resp.status_code == 200


# ----------------------------------------------------------------------
# GET /api/user/server-metrics
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_metrics_forbidden_for_regular_user(client, user_auth_mock, metrics_override):
    resp = await client.get("/api/user/server-metrics")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_server_metrics_ok_for_moderator(client, user_auth_mock, test_user, db_session, metrics_override):
    from database.models import ServerMetric
    from services.server_metrics import _floor_to_window

    test_user.login_name = MODERATOR_LOGIN
    window = _floor_to_window(datetime.now(UTC))
    db_session.add(ServerMetric(bucket_ts=window, cpu_avg=30.0, cpu_max=60.0, disk_used_percent=70.0))
    await db_session.commit()

    resp = await client.get("/api/user/server-metrics", params={"from": (window - timedelta(hours=1)).isoformat()})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["points"]) == 1
    assert data["points"][0]["cpu_avg"] == 30.0
    # live-сэмпл из Redis: диск 95% → critical
    assert [(a["metric"], a["severity"]) for a in data["alerts"]] == [("disk", "critical")]


# ----------------------------------------------------------------------
# GET /api/user/staff-badges
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_badges_forbidden_for_regular_user(client, user_auth_mock, metrics_override):
    resp = await client.get("/api/user/staff-badges")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_staff_badges_counts_for_moderator(client, user_auth_mock, test_user, db_session, metrics_override):
    test_user.login_name = MODERATOR_LOGIN
    db_session.add_all(
        [
            CharacterInfo(name="streamer_a", file_id=uuid4(), approved=None),  # ожидает проверки
            CharacterInfo(name="streamer_b", file_id=uuid4(), approved=True),  # проверена
        ]
    )
    await db_session.commit()

    resp = await client.get("/api/user/staff-badges")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending_references"] == 1
    assert data["server_alerts"] == 1  # live-сэмпл: диск 95% при лимите 90%


@pytest.mark.asyncio
async def test_server_metrics_requires_login(client):
    resp = await client.get("/api/user/server-metrics")
    assert resp.status_code == 401
