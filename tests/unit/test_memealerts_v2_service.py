# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты MemealertsV2Service (services/memes_v2.py): бонусы, саппортеры, кэш."""

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import httpx
import pytest

from exceptions import (
    MADuplicateUserError,
    MAInvalidTokenError,
    MANotSupporterError,
    MAUnavailableError,
    MAUserNotFoundError,
    MAValidationRespError,
)
from schemas.memealerts import MASupporter, MASupportersList
from services.memes_v2 import MemealertsV2Service, Tokens


class FakeAsyncClient:
    """Подмена httpx.AsyncClient для GET/POST запросов к MA API."""

    response: httpx.Response | Exception | None = None
    last_request: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def _respond(self, url, **kwargs):
        FakeAsyncClient.last_request = {"url": url, **kwargs}
        if isinstance(FakeAsyncClient.response, Exception):
            raise FakeAsyncClient.response
        assert FakeAsyncClient.response is not None
        return FakeAsyncClient.response

    async def post(self, url, **kwargs):
        return await self._respond(url, **kwargs)

    async def get(self, url, **kwargs):
        return await self._respond(url, **kwargs)


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr("services.memes_v2.httpx.AsyncClient", FakeAsyncClient)
    FakeAsyncClient.response = None
    FakeAsyncClient.last_request = None
    return FakeAsyncClient


def make_service() -> MemealertsV2Service:
    return MemealertsV2Service(db_session_factory=lambda: None)


def make_token() -> Tokens[str]:
    return Tokens(access_token="tok", refresh_token="r", expires_in=3600)


def make_supporter(sid: str = "a" * 24, name: str = "nick", link: str | None = None) -> MASupporter:
    return MASupporter.model_validate(
        {
            "supporterId": sid,
            "supporterName": name,
            "supporterLink": link,
            "spent": 0,
            "purchased": 0,
            "mutedByStreamer": False,
            "joined": None,
        }
    )


def make_supporters_list(n: int, total: int | None = None) -> MASupportersList:
    return MASupportersList(
        data=[make_supporter(sid=f"{i:024x}", name=f"user{i}") for i in range(n)],
        total=total if total is not None else n,
    )


# ── is_id ───────────────────────────────────────────────────────────────────


def test_is_id_accepts_24_hex():
    service = make_service()
    assert service.is_id("0123456789abcdef01234567") is True


def test_is_id_rejects_non_hex_and_wrong_length():
    service = make_service()
    assert service.is_id("g" * 24) is False  # не hex
    assert service.is_id("a" * 23) is False  # короткий
    assert service.is_id("A" * 24) is False  # верхний регистр
    assert service.is_id("") is False


# ── _pick_user_from_list ────────────────────────────────────────────────────


def test_pick_user_by_name():
    service = make_service()
    user = make_supporter(name="nick")

    assert service._pick_user_from_list([user], "nick") is user  # username приходит уже lowercase от вызывающего


def test_pick_user_by_link():
    service = make_service()
    user = make_supporter(name="nick", link="nlink")

    assert service._pick_user_from_list([user], "nlink") is user


def test_pick_user_no_match():
    service = make_service()
    assert service._pick_user_from_list([make_supporter(name="x")], "y") is None


def test_pick_user_duplicate_different_users_raises():
    service = make_service()
    first = make_supporter(sid="a" * 24, name="dup")
    second = make_supporter(sid="b" * 24, name="other", link="dup")

    with pytest.raises(MADuplicateUserError):
        service._pick_user_from_list([first, second], "dup")


def test_pick_user_same_value_within_one_user_is_not_duplicate():
    """link == name у одного пользователя — не дубликат."""
    service = make_service()
    user = make_supporter(name="dup", link="dup")

    assert service._pick_user_from_list([user], "dup") is user


# ── find_and_give_bonus ─────────────────────────────────────────────────────


async def test_bonus_by_id_skips_searches():
    service = make_service()
    ma_id = "0123456789abcdef01234567"
    give = AsyncMock(return_value=True)
    service._give_bonus = give
    service.search_supporter_from_db = AsyncMock()
    service.find_user_in_supporters = AsyncMock()

    assert await service.find_and_give_bonus(make_token(), ma_id, 100) is True

    service.search_supporter_from_db.assert_not_awaited()
    service.find_user_in_supporters.assert_not_awaited()
    user_id = give.await_args.kwargs["user_id"]
    assert str(user_id) == ma_id


async def test_bonus_from_db_cache():
    service = make_service()
    service.search_supporter_from_db = AsyncMock(return_value=SimpleNamespace(id="b" * 24))
    give = AsyncMock(return_value=True)
    service._give_bonus = give
    service.find_user_in_supporters = AsyncMock()

    assert await service.find_and_give_bonus(make_token(), "nick", 100) is True

    service.find_user_in_supporters.assert_not_awaited()
    assert give.await_args.kwargs["value"] == 100


async def test_bonus_db_multiple_results_falls_back_to_api():
    from sqlalchemy.exc import MultipleResultsFound

    service = make_service()
    service.search_supporter_from_db = AsyncMock(side_effect=MultipleResultsFound())
    api_user = make_supporter(name="nick")
    service.find_user_in_supporters = AsyncMock(return_value=api_user)
    give = AsyncMock(return_value=True)
    service._give_bonus = give

    assert await service.find_and_give_bonus(make_token(), "nick", 100) is True

    service.find_user_in_supporters.assert_awaited_once()
    assert give.await_args.kwargs["user_id"] == api_user.supporter_id


async def test_bonus_db_error_falls_back_to_api():
    service = make_service()
    service.search_supporter_from_db = AsyncMock(side_effect=RuntimeError("db down"))
    api_user = make_supporter(name="nick")
    service.find_user_in_supporters = AsyncMock(return_value=api_user)
    service._give_bonus = AsyncMock(return_value=True)

    assert await service.find_and_give_bonus(make_token(), "nick", 100) is True


async def test_bonus_not_found_returns_false():
    service = make_service()
    service.search_supporter_from_db = AsyncMock(return_value=None)
    service.find_user_in_supporters = AsyncMock(return_value=None)
    service._give_bonus = AsyncMock()

    assert await service.find_and_give_bonus(make_token(), "ghost", 100) is False

    service._give_bonus.assert_not_awaited()


# ── give_bonus ──────────────────────────────────────────────────────────────


async def test_give_bonus_wraps_search():
    service = make_service()
    service.find_and_give_bonus = AsyncMock(return_value=True)

    assert await service.give_bonus(make_token(), "streamer", "nick", 50) is True

    service.find_and_give_bonus.assert_awaited_once_with(ANY, "nick", 50)


async def test_give_bonus_propagates_ma_error():
    service = make_service()
    service.find_and_give_bonus = AsyncMock(side_effect=MAUserNotFoundError)

    with pytest.raises(MAUserNotFoundError):
        await service.give_bonus(make_token(), "streamer", "nick", 50)


# ── _give_bonus ─────────────────────────────────────────────────────────────


async def test_give_bonus_value_validation():
    service = make_service()
    for bad in (0, -1, 1001):
        with pytest.raises(ValueError):
            await service._give_bonus(make_token(), user_id="a" * 24, value=bad)


async def test_give_bonus_unavailable(fake_http):
    fake_http.response = httpx.Response(500, json={})
    with pytest.raises(MAUnavailableError):
        await make_service()._give_bonus(make_token(), user_id="a" * 24, value=10)


async def test_give_bonus_user_not_found(fake_http):
    fake_http.response = httpx.Response(404, json={})
    with pytest.raises(MAUserNotFoundError):
        await make_service()._give_bonus(make_token(), user_id="a" * 24, value=10)


async def test_give_bonus_not_a_supporter(fake_http):
    from services.memes_v2 import MA_NOT_A_SUPPORTER_MESSAGE

    fake_http.response = httpx.Response(403, json={"message": MA_NOT_A_SUPPORTER_MESSAGE})
    with pytest.raises(MANotSupporterError):
        await make_service()._give_bonus(make_token(), user_id="a" * 24, value=10)


async def test_give_bonus_invalid_token(fake_http):
    fake_http.response = httpx.Response(401, json={"message": "other"})
    with pytest.raises(MAInvalidTokenError):
        await make_service()._give_bonus(make_token(), user_id="a" * 24, value=10)


async def test_give_bonus_success(fake_http):
    fake_http.response = httpx.Response(200, json={"result": True})
    result = await make_service()._give_bonus(make_token(), user_id="a" * 24, value=10)
    assert result is True
    body = fake_http.last_request["json"]
    assert body == {"userId": "a" * 24, "value": 10}


async def test_give_bonus_invalid_response_body(fake_http):
    fake_http.response = httpx.Response(200, json={"strange": 1})
    with pytest.raises(MAValidationRespError):
        await make_service()._give_bonus(make_token(), user_id="a" * 24, value=10)


# ── get_user_info ───────────────────────────────────────────────────────────


async def test_get_user_info_success(fake_http):
    fake_http.response = httpx.Response(
        200,
        json={
            "data": {
                "id": "a" * 24,
                "code": "u",
                "name": "User",
                "avatar": "",
                "email": "u@example.com",
                "channel": None,
            }
        },
    )

    info = await make_service().get_user_info(make_token())

    assert info.code == "u"


async def test_get_user_info_unavailable(fake_http):
    fake_http.response = httpx.Response(502, json={})
    with pytest.raises(MAUnavailableError):
        await make_service().get_user_info(make_token())


async def test_get_user_info_invalid_token(fake_http):
    fake_http.response = httpx.Response(401, json={})
    with pytest.raises(MAInvalidTokenError):
        await make_service().get_user_info(make_token())


async def test_get_user_info_invalid_payload(fake_http):
    fake_http.response = httpx.Response(200, json={"data": {"id": "not-hex"}})
    with pytest.raises(MAValidationRespError):
        await make_service().get_user_info(make_token())


# ── _get_supporters / load_supporters ───────────────────────────────────────


async def test_get_supporters_param_validation():
    service = make_service()
    with pytest.raises(ValueError):
        await service._get_supporters(make_token(), skip=-1)
    with pytest.raises(ValueError):
        await service._get_supporters(make_token(), limit=0)
    with pytest.raises(ValueError):
        await service._get_supporters(make_token(), limit=101)


async def test_load_supporters_single_page():
    service = make_service()
    page = make_supporters_list(3)
    service._get_supporters = AsyncMock(return_value=page)
    service.save_all_supporters_into_db = AsyncMock()

    items = await service.load_supporters(make_token(), query="nick")

    assert len(items) == 3
    service._get_supporters.assert_awaited_once_with(ma_token=ANY, limit=100, skip=0, query="nick")
    # фоновая задача сохранения отработала
    await asyncio.gather(*service._background_tasks)
    service.save_all_supporters_into_db.assert_awaited_once_with(items)


async def test_load_supporters_multipage():
    service = make_service()
    first = make_supporters_list(100, total=250)
    second = make_supporters_list(100, total=250)
    third = make_supporters_list(50, total=250)
    service._get_supporters = AsyncMock(side_effect=[first, second, third])
    service.save_all_supporters_into_db = AsyncMock()

    items = await service.load_supporters(make_token())

    assert len(items) == 250
    assert service._get_supporters.await_count == 3
    skips = [c.kwargs["skip"] for c in service._get_supporters.await_args_list]
    assert skips == [0, 100, 200]


async def test_save_all_supporters_empty_list_no_db():
    service = MemealertsV2Service(db_session_factory=_raising_factory)
    await service.save_all_supporters_into_db([])


def _raising_factory():
    raise AssertionError("БД не должна использоваться для пустого списка")


# ── find_user_in_supporters ─────────────────────────────────────────────────


async def test_find_in_supporters_targeted_hit():
    service = make_service()
    target = make_supporter(name="nick")
    service.load_supporters = AsyncMock(return_value=[target])

    found = await service.find_user_in_supporters(make_token(), "nick")

    assert found is target
    service.load_supporters.assert_awaited_once_with(ANY, query="nick")


async def test_find_in_supporters_falls_back_to_full_scan():
    service = make_service()
    target = make_supporter(name="nick")
    service.load_supporters = AsyncMock(side_effect=[[], [target]])

    found = await service.find_user_in_supporters(make_token(), "nick")

    assert found is target
    assert service.load_supporters.await_count == 2
    assert service.load_supporters.await_args_list[1].kwargs.get("query") is None


async def test_find_in_supporters_targeted_error_falls_back():
    service = make_service()
    target = make_supporter(name="nick")
    service.load_supporters = AsyncMock(side_effect=[RuntimeError("api boom"), [target]])

    found = await service.find_user_in_supporters(make_token(), "nick")

    assert found is target


async def test_find_in_supporters_duplicate_propagates():
    """MADuplicateUserError из точечного поиска не глушится фолбэком."""
    service = make_service()
    service.load_supporters = AsyncMock(
        return_value=[
            make_supporter(sid="a" * 24, name="dup"),
            make_supporter(sid="b" * 24, name="x", link="dup"),
        ]
    )

    with pytest.raises(MADuplicateUserError):
        await service.find_user_in_supporters(make_token(), "dup")

    assert service.load_supporters.await_count == 1


async def test_safe_save_supporters_swallows_errors():
    service = make_service()
    service.save_all_supporters_into_db = AsyncMock(side_effect=RuntimeError("db down"))

    await service._safe_save_supporters([make_supporter()])  # не должен бросить
