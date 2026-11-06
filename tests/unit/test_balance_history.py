# ruff: noqa: S101

"""Тесты истории баланса: записи операций (стикеры/депозит) + API + страница."""

from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa

from config import settings
from database.models import BalanceOperation, User
from main import app
from routers.api.admin_api import admin_auth
from routers.security_helpers import user_auth
from schemas.enums import BalanceOperationAction, BalanceOperationType
from tests.unit.test_stickers_service import SessionCtxNoBegin, fake_ai, fake_s3, make_service


@pytest.fixture
def admin_auth_mock():
    app.dependency_overrides[admin_auth] = lambda: None
    yield
    app.dependency_overrides.pop(admin_auth, None)


async def _add_op(db_session, twitch_id: str, amount: str = "10.00", balance_after: str = "10.00") -> int:
    op = BalanceOperation(
        twitch_id=twitch_id,
        type=BalanceOperationType.DEPOSIT,
        action=BalanceOperationAction.ADMIN_DEPOSIT,
        amount=Decimal(amount),
        balance_after=Decimal(balance_after),
    )
    db_session.add(op)
    await db_session.commit()
    await db_session.refresh(op)
    return op.id


# ----------------------------------------------------------------------
# Запись операции при генерации ИИ-стикера
# ----------------------------------------------------------------------


async def test_sticker_save_to_db_writes_balance_operation(db_session, test_user):
    service = make_service(db=lambda: SessionCtxNoBegin(db_session), s3=fake_s3(), ai=fake_ai())
    file_id = uuid4()
    cost = 0.5
    test_user.total_deposited = Decimal("100.00")
    await db_session.commit()

    await service._save_to_db(file_id, "котик", "chatter", test_user, cost)  # noqa: SLF001
    await db_session.refresh(test_user, attribute_names=["total_spent", "total_deposited"])

    op = await db_session.scalar(sa.select(BalanceOperation).where(BalanceOperation.twitch_id == test_user.twitch_id))
    assert op is not None
    assert op.type == BalanceOperationType.SPEND
    assert op.action == BalanceOperationAction.AI_STICKER
    # amount — полная сумма с наценкой сервиса (multiplier + single_call)
    raw_delta = Decimal(cost * settings.ai.cost_multiplier + settings.ai.cost_single_call).quantize(Decimal("0.01"))
    assert op.amount == raw_delta
    assert op.balance_after == (Decimal("100.00") - raw_delta)
    assert op.details["chatter"] == "chatter"
    assert op.details["prompt"] == "котик"


# ----------------------------------------------------------------------
# Запись операции при пополнении через /api/admin/balance/deposit
# ----------------------------------------------------------------------


async def test_deposit_writes_balance_operation(client, db_session, test_user, admin_auth_mock):  # noqa: ARG001
    resp = await client.post(
        "/api/admin/balance/deposit",
        json={"twitch_login": test_user.login_name, "amount": "50"},
    )
    assert resp.status_code == 200
    assert Decimal(resp.json()["deposited"]) == Decimal("50")

    op = await db_session.scalar(sa.select(BalanceOperation).where(BalanceOperation.twitch_id == test_user.twitch_id))
    assert op is not None
    assert op.type == BalanceOperationType.DEPOSIT
    assert op.action == BalanceOperationAction.ADMIN_DEPOSIT
    assert op.amount == Decimal("50.00")
    assert op.balance_after == Decimal("50.00")
    assert op.details["admin"] == settings.app.admin_api_login


async def test_deposit_zero_does_not_write_operation(client, db_session, test_user, admin_auth_mock):  # noqa: ARG001
    test_user.total_deposited = Decimal("1000.00")  # баланс уже на капе
    await db_session.commit()

    resp = await client.post(
        "/api/admin/balance/deposit",
        json={"twitch_login": test_user.login_name, "amount": "50"},
    )

    assert resp.status_code == 200
    assert resp.json()["deposited"] == "0.00"
    ops = (await db_session.execute(sa.select(BalanceOperation))).scalars().all()
    assert ops == []


# ----------------------------------------------------------------------
# API /api/user/balance/operations
# ----------------------------------------------------------------------


async def test_operations_requires_auth(client):
    resp = await client.get("/api/user/balance/operations")
    assert resp.status_code in (401, 403)


async def test_operations_pagination_and_isolation(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    other = User(
        twitch_id="999999",
        login_name="other_streamer",
        profile_image_url="https://example.com/o.png",
        access_token="t",
        refresh_token="t",
    )
    db_session.add(other)
    await db_session.commit()

    first_id = await _add_op(db_session, test_user.twitch_id, "10.00", "10.00")
    second_id = await _add_op(db_session, test_user.twitch_id, "5.00", "5.00")
    await _add_op(db_session, other.twitch_id, "7.00", "7.00")  # чужая операция

    resp = await client.get("/api/user/balance/operations", params={"limit": 1})
    assert resp.status_code == 200
    page1 = resp.json()
    assert [item["id"] for item in page1["items"]] == [second_id]  # новее → старше
    assert page1["next_cursor"] == second_id

    resp = await client.get("/api/user/balance/operations", params={"limit": 10, "before_id": page1["next_cursor"]})
    page2 = resp.json()
    assert [item["id"] for item in page2["items"]] == [first_id]
    assert page2["next_cursor"] is None
    assert page2["items"][0]["type"] == "deposit"
    assert page2["items"][0]["action"] == "admin_deposit"


# ----------------------------------------------------------------------
# Страница /balance-history
# ----------------------------------------------------------------------


async def test_balance_history_page_requires_auth(client, session_override):  # noqa: ARG001
    resp = await client.get("/balance-history", follow_redirects=False)
    assert resp.status_code in (302, 307)


async def test_balance_history_page_renders(client, user_auth_mock, test_user):  # noqa: ARG001
    resp = await client.get("/balance-history")
    assert resp.status_code == 200
    assert "История баланса" in resp.text


# ----------------------------------------------------------------------
# Админка /admin (секция пополнения баланса)
# ----------------------------------------------------------------------


async def test_admin_page_forbidden_for_non_admin(client, user_auth_mock, test_user, admin_auth_mock):  # noqa: ARG001
    resp = await client.get("/admin")
    assert resp.status_code == 403


async def test_admin_page_renders_for_admin(client, db_session, admin_auth_mock):  # noqa: ARG001
    admin_user = User(
        twitch_id="777777",
        login_name="quantum075",
        profile_image_url="https://example.com/admin.png",
        access_token="t",
        refresh_token="t",
    )
    db_session.add(admin_user)
    await db_session.commit()
    # Прогреваем lazy-relationship'ы — base.html читает их синхронно (как test_user)
    await db_session.refresh(admin_user, attribute_names=["settings", "memealerts", "links", "tts", "telegram"])
    app.dependency_overrides[user_auth] = lambda: admin_user
    try:
        resp = await client.get("/admin")
        assert resp.status_code == 200
        assert "Пополнение баланса" in resp.text
        assert "/static/js/admin.js" in resp.text
    finally:
        app.dependency_overrides.pop(user_auth, None)
