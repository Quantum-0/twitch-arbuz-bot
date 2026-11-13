# ruff: noqa: S101

"""Тесты истории покупок мемкоинов: дедуп вебхука, запись исходов, API, страница."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from memealerts.types.exceptions import MATokenExpiredError, MAUserNotFoundError

from database.models import MemealertsDeposit, User
from exceptions import (
    MADuplicateUserError,
    MAInvalidTokenError,
    MANotSupporterError,
    MATokenInvalidError,
    MAUnavailableError,
)
from schemas.api import StatsType
from schemas.enums import MemealertsDepositError, MemealertsDepositStatus
from services.eventsub_service import TwitchEventSubService

# ----------------------------------------------------------------------
# Сервис: дедуп-маркер + фиксация исхода
# ----------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value: Any):
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """AsyncSession-заглушка: запоминает execute-вызовы, выдаёт результаты из очереди."""

    def __init__(self, results: list[Any]):
        self._results = list(results)
        self.executed: list[Any] = []

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, stmt: Any) -> Any:
        self.executed.append(stmt)
        return self._results.pop(0) if self._results else SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self) -> None:
        pass


def make_db_factory(*results_per_session: list[Any]) -> tuple[Any, list[_FakeSession]]:
    """Фабрика сессий: каждый вызов (маркер → фиксация) получает свою сессию."""
    sessions: list[_FakeSession] = []
    queue = list(results_per_session)

    def factory() -> _FakeSession:
        session = _FakeSession(queue.pop(0) if queue else [])
        sessions.append(session)
        return session

    return factory, sessions


def build_service(db_factory: Any) -> TwitchEventSubService:
    service = object.__new__(TwitchEventSubService)
    service._statistics = SimpleNamespace(inc=Mock())  # noqa: SLF001
    service._db_session_factory = db_factory  # noqa: SLF001
    service._memealerts_auth = SimpleNamespace(get_token_of_user=AsyncMock(return_value="v2-token"))  # noqa: SLF001
    service._memealerts_v2 = SimpleNamespace(give_bonus=AsyncMock(return_value=True))  # noqa: SLF001
    service._memealerts = SimpleNamespace(give_bonus=AsyncMock(return_value=True))  # noqa: SLF001
    service._chatbot = SimpleNamespace(send_message=AsyncMock())  # noqa: SLF001
    service._twitch = SimpleNamespace(  # noqa: SLF001
        cancel_redemption=AsyncMock(),
        fulfill_redemption=AsyncMock(),
    )
    return service


def build_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=1000,
        twitch_id="123",
        login_name="streamer",
        memealerts=SimpleNamespace(
            access_token="oauth-token",
            memealerts_token="legacy-token",
            coins_for_reward=2,
            memecoin_name_accusative=None,
            memecoin_name_genitive=None,
            memecoin_name_genitive_multiple=None,
        ),
    )


def build_payload() -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(
            user_input="viewer-id",
            user_name="viewer",
            user_login="viewer",
            user_id=42,
            redemption_id=UUID("11111111-1111-1111-1111-111111111111"),
            reward=SimpleNamespace(cost=1000),
        ),
        subscription=SimpleNamespace(condition=SimpleNamespace(reward_id="reward")),
    )


@pytest.mark.asyncio
async def test_duplicate_webhook_skips_crediting() -> None:
    # ON CONFLICT: строка не вставлена → повторная доставка вебхука.
    db_factory, sessions = make_db_factory([_FakeResult(None)])
    service = build_service(db_factory)

    await service.reward_buy_memealerts(user=build_user(), payload=build_payload())  # noqa: SLF001

    service._memealerts_v2.give_bonus.assert_not_awaited()  # noqa: SLF001
    service._twitch.fulfill_redemption.assert_not_awaited()  # noqa: SLF001
    # Дубль не инкрементирует received — только отдельный счётчик duplicate.
    service._statistics.inc.assert_called_once_with(  # noqa: SLF001
        StatsType.REWARD_MEMECOINS, subtype="duplicate"
    )
    assert len(sessions) == 1  # фикса исхода не было


@pytest.mark.asyncio
async def test_marker_inserts_payload_fields() -> None:
    # Маркер пишет данные вебхука: цену награды из event.reward.cost (на момент
    # выкупа), чаттера, введённый ник и настройки канала.
    db_factory, sessions = make_db_factory([_FakeResult(7)])
    service = build_service(db_factory)

    await service.reward_buy_memealerts(user=build_user(), payload=build_payload())  # noqa: SLF001

    marker = sessions[0].executed[0].compile().params
    assert marker["twitch_id"] == "123"
    assert marker["redemption_id"] == UUID("11111111-1111-1111-1111-111111111111")
    assert marker["chatter_twitch_id"] == "42"
    assert marker["chatter_login"] == "viewer"
    assert marker["memealerts_username"] == "viewer-id"
    assert marker["points_spent"] == 1000
    assert marker["memecoins"] == 2
    assert marker["status"] == MemealertsDepositStatus.PENDING.value


@pytest.mark.asyncio
async def test_reward_without_cost_records_null_points() -> None:
    # Защита от будущих изменений формата: reward не приехал → цена NULL.
    db_factory, sessions = make_db_factory([_FakeResult(7)])
    service = build_service(db_factory)
    payload = build_payload()
    payload.event.reward = None

    await service.reward_buy_memealerts(user=build_user(), payload=payload)  # noqa: SLF001

    assert sessions[0].executed[0].compile().params["points_spent"] is None


@pytest.mark.asyncio
async def test_marker_db_error_does_not_block_crediting() -> None:
    def broken_factory() -> Any:
        raise RuntimeError("db down")

    service = build_service(broken_factory)

    await service.reward_buy_memealerts(user=build_user(), payload=build_payload())  # noqa: SLF001

    service._memealerts_v2.give_bonus.assert_awaited_once()  # noqa: SLF001
    service._twitch.fulfill_redemption.assert_awaited_once()  # noqa: SLF001


@pytest.mark.asyncio
async def test_success_records_deposit() -> None:
    db_factory, sessions = make_db_factory([_FakeResult(7)])
    service = build_service(db_factory)

    await service.reward_buy_memealerts(user=build_user(), payload=build_payload())  # noqa: SLF001

    service._twitch.fulfill_redemption.assert_awaited_once()  # noqa: SLF001
    assert len(sessions) == 2  # маркер + фиксация исхода
    params = sessions[1].executed[0].compile().params
    assert params["status"] == MemealertsDepositStatus.SUCCEEDED.value
    assert params["error"] is None


@pytest.mark.asyncio
async def test_supporter_not_found_records_failure() -> None:
    db_factory, sessions = make_db_factory([_FakeResult(7)])
    service = build_service(db_factory)
    service._memealerts_v2.give_bonus.return_value = False  # noqa: SLF001

    await service.reward_buy_memealerts(user=build_user(), payload=build_payload())  # noqa: SLF001

    service._twitch.cancel_redemption.assert_awaited_once()  # noqa: SLF001
    params = sessions[1].executed[0].compile().params
    assert params["status"] == MemealertsDepositStatus.FAILED.value
    assert params["error"] == MemealertsDepositError.USER_NOT_FOUND.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        (MAUserNotFoundError, MemealertsDepositError.USER_NOT_FOUND),
        (MANotSupporterError, MemealertsDepositError.NOT_SUPPORTER),
        (MADuplicateUserError("dup"), MemealertsDepositError.DUPLICATE_USER),
        (MATokenInvalidError, MemealertsDepositError.TOKEN_INVALID),
        (MAUnavailableError, MemealertsDepositError.MA_UNAVAILABLE),
        (RuntimeError("boom"), MemealertsDepositError.UNEXPECTED),
    ],
)
async def test_api_errors_record_reason(side_effect: Any, expected_error: MemealertsDepositError) -> None:
    db_factory, sessions = make_db_factory([_FakeResult(7)])
    service = build_service(db_factory)
    service._memealerts_v2.give_bonus.side_effect = side_effect  # noqa: SLF001

    await service.reward_buy_memealerts(user=build_user(), payload=build_payload())  # noqa: SLF001

    service._twitch.cancel_redemption.assert_awaited_once()  # noqa: SLF001
    params = sessions[1].executed[0].compile().params
    assert params["status"] == MemealertsDepositStatus.FAILED.value
    assert params["error"] == expected_error.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        (MATokenExpiredError, MemealertsDepositError.TOKEN_EXPIRED),
        (MAInvalidTokenError, MemealertsDepositError.TOKEN_INVALID),
    ],
)
async def test_v2_fallback_errors_without_v1_token_record_reason(
    side_effect: Any, expected_error: MemealertsDepositError
) -> None:
    # Эти ошибки входят в MEMEALERTS_V2_FALLBACK_ERRORS: при живом v1-токене
    # уходит в фолбэк; mapping провала проверяем без v1-токена (re-raise).
    db_factory, sessions = make_db_factory([_FakeResult(7)])
    service = build_service(db_factory)
    service._memealerts_v2.give_bonus.side_effect = side_effect  # noqa: SLF001
    user = build_user()
    user.memealerts.memealerts_token = None

    await service.reward_buy_memealerts(user=user, payload=build_payload())  # noqa: SLF001

    service._memealerts.give_bonus.assert_not_awaited()  # noqa: SLF001
    service._twitch.cancel_redemption.assert_awaited_once()  # noqa: SLF001
    params = sessions[1].executed[0].compile().params
    assert params["status"] == MemealertsDepositStatus.FAILED.value
    assert params["error"] == expected_error.value


@pytest.mark.asyncio
async def test_v1_path_records_deposit() -> None:
    db_factory, sessions = make_db_factory([_FakeResult(7)])
    service = build_service(db_factory)
    user = build_user()
    user.memealerts.access_token = None

    await service.reward_buy_memealerts(user=user, payload=build_payload())  # noqa: SLF001

    service._memealerts.give_bonus.assert_awaited_once()  # noqa: SLF001
    service._memealerts_v2.give_bonus.assert_not_awaited()  # noqa: SLF001
    params = sessions[1].executed[0].compile().params
    assert params["status"] == MemealertsDepositStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_branch_exception_still_finishes_deposit() -> None:
    # Исключение из чат-бота внутри except-ветки не должно оставлять строку
    # навсегда в pending: finally фиксирует уже присвоенный исход.
    db_factory, sessions = make_db_factory([_FakeResult(7)])
    service = build_service(db_factory)
    service._memealerts_v2.give_bonus.side_effect = MAUserNotFoundError  # noqa: SLF001
    service._chatbot.send_message.side_effect = RuntimeError("chat down")  # noqa: SLF001

    with pytest.raises(RuntimeError, match="chat down"):
        await service.reward_buy_memealerts(user=build_user(), payload=build_payload())  # noqa: SLF001

    params = sessions[1].executed[0].compile().params
    assert params["status"] == MemealertsDepositStatus.FAILED.value
    assert params["error"] == MemealertsDepositError.USER_NOT_FOUND.value


@pytest.mark.asyncio
async def test_success_status_survives_fulfill_failure() -> None:
    # Мемкоины начислены до fulfill. Сбой fulfill ловится generic-веткой каскада
    # (как в старом коде: cancel + сообщение), но история обязана остаться
    # SUCCEEDED — факт начисления не изменился.
    db_factory, sessions = make_db_factory([_FakeResult(7)])
    service = build_service(db_factory)
    service._twitch.fulfill_redemption.side_effect = RuntimeError("twitch down")  # noqa: SLF001

    await service.reward_buy_memealerts(user=build_user(), payload=build_payload())  # noqa: SLF001

    service._twitch.cancel_redemption.assert_awaited_once()  # noqa: SLF001
    params = sessions[1].executed[0].compile().params
    assert params["status"] == MemealertsDepositStatus.SUCCEEDED.value
    assert params["error"] is None


# ----------------------------------------------------------------------
# API /api/user/memealerts/deposits
# ----------------------------------------------------------------------


async def _add_deposit(db_session: Any, twitch_id: str, **overrides: Any) -> int:
    values: dict[str, Any] = {
        "redemption_id": uuid4(),
        "chatter_twitch_id": "42",
        "chatter_login": "viewer",
        "memealerts_username": "viewer_ma",
        "points_spent": 1000,
        "memecoins": 2,
        "status": MemealertsDepositStatus.SUCCEEDED,
    }
    values.update(overrides)
    deposit = MemealertsDeposit(twitch_id=twitch_id, **values)
    db_session.add(deposit)
    await db_session.commit()
    await db_session.refresh(deposit)
    return deposit.id


async def test_deposits_requires_auth(client):  # noqa: ARG001
    resp = await client.get("/api/user/memealerts/deposits")
    assert resp.status_code in (401, 403)


async def test_deposits_pagination_and_isolation(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    other = User(
        twitch_id="999999",
        login_name="other_streamer",
        profile_image_url="https://example.com/o.png",
        access_token="t",
        refresh_token="t",
    )
    db_session.add(other)
    await db_session.commit()

    ok_id = await _add_deposit(db_session, test_user.twitch_id)
    fail_id = await _add_deposit(
        db_session,
        test_user.twitch_id,
        redemption_id=uuid4(),
        status=MemealertsDepositStatus.FAILED,
        error=MemealertsDepositError.NOT_SUPPORTER,
        points_spent=None,
    )
    await _add_deposit(db_session, other.twitch_id)  # чужая покупка

    resp = await client.get("/api/user/memealerts/deposits", params={"limit": 1})
    assert resp.status_code == 200
    page1 = resp.json()
    assert [item["id"] for item in page1["items"]] == [fail_id]  # новее → старше
    assert page1["next_cursor"] == fail_id
    failed = page1["items"][0]
    assert failed["status"] == "failed"
    assert failed["error"] == "not_supporter"
    assert failed["points_spent"] is None

    resp = await client.get("/api/user/memealerts/deposits", params={"limit": 10, "before_id": page1["next_cursor"]})
    page2 = resp.json()
    assert [item["id"] for item in page2["items"]] == [ok_id]
    assert page2["next_cursor"] is None
    item = page2["items"][0]
    assert item["status"] == "succeeded"
    assert item["chatter_login"] == "viewer"
    assert item["memealerts_username"] == "viewer_ma"
    assert item["points_spent"] == 1000
    assert item["memecoins"] == 2


# ----------------------------------------------------------------------
# Страница /panel/memealerts
# ----------------------------------------------------------------------


async def test_panel_memealerts_history_visible_for_v2(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    test_user.memealerts.access_token = "token"
    await db_session.commit()

    resp = await client.get("/panel/memealerts")

    assert resp.status_code == 200
    assert "История пополнений" in resp.text
    assert "Стоимость 1 мемкоина" in resp.text


async def test_panel_memealerts_history_hidden_without_v2(client, user_auth_mock, test_user):  # noqa: ARG001
    resp = await client.get("/panel/memealerts")

    assert resp.status_code == 200
    assert "История пополнений" not in resp.text
