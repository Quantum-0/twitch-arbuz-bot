# ruff: noqa: S101

"""Тесты нечёткого поиска пользователей (utils/user_search.py + /api/user/search)."""

import inspect
from contextlib import contextmanager

import pytest

from database.models import User
from main import app
from routers.api.user import search as search_module
from utils.user_search import _escape_like, normalize_query, search_users


class FakeChatBot:
    def __init__(self, active: list[tuple[str, str]]):
        self._active = active

    async def get_last_active_users(self, user) -> list[tuple[str, str]]:  # noqa: ARG002
        return self._active


@contextmanager
def chat_bot_override(fake):
    dep = inspect.signature(search_module.search).parameters["chat_bot"].annotation.__metadata__[0].dependency
    app.dependency_overrides[dep] = lambda: fake
    try:
        yield
    finally:
        app.dependency_overrides.pop(dep, None)


@pytest.fixture(autouse=True)
def chat_bot_stub():
    """Роут резолвит Provide[Container.chat_bot] даже вне context=bans — в тестах
    контейнер не поднят, поэтому подменяем зависимость пустой заглушкой."""
    with chat_bot_override(FakeChatBot([])):
        yield


async def _make_user(db_session, login: str, twitch_id: str) -> None:
    db_session.add(
        User(
            twitch_id=twitch_id,
            login_name=login,
            profile_image_url=f"https://example.com/{login}.png",
            access_token="test-access-token",
            refresh_token="test-refresh-token",
        )
    )
    await db_session.commit()


# ----------------------------------------------------------------------
# normalize_query
# ----------------------------------------------------------------------


def test_normalize_query():
    assert normalize_query("Quantum075") == "quantum075"
    assert normalize_query("  @Quantum075  ") == "quantum075"


# ----------------------------------------------------------------------
# Сервис search_users
# ----------------------------------------------------------------------


async def test_search_exact_match_first(db_session):
    await _make_user(db_session, "quantum075", "1001")
    await _make_user(db_session, "quantum075bot", "1002")

    results = await search_users(db_session, "quantum075")

    assert results[0]["login"] == "quantum075"
    assert [r["login"] for r in results] == ["quantum075", "quantum075bot"]


async def test_search_finds_typo(db_session):
    # kwantum075 → quantum075: опечатка ловится через pg_trgm + rapidfuzz
    await _make_user(db_session, "quantum075", "1001")

    results = await search_users(db_session, "kwantum075")

    assert [r["login"] for r in results] == ["quantum075"]


async def test_search_substring(db_session):
    await _make_user(db_session, "unrelated_guy", "1004")

    results = await search_users(db_session, "related")

    assert [r["login"] for r in results] == ["unrelated_guy"]


def test_escape_like():
    # % и _ в запросе — литералы, а не wildcards ILIKE
    assert _escape_like("100%_a") == "100\\%\\_a"
    assert _escape_like("plain") == "plain"


async def test_search_excludes_logins(db_session):
    await _make_user(db_session, "quantum075", "1001")
    await _make_user(db_session, "quantum075bot", "1002")
    await _make_user(db_session, "kwantum", "1003")

    results = await search_users(db_session, "quantum", exclude_logins={"quantum075", "quantum075bot"})

    assert [r["login"] for r in results] == ["kwantum"]


async def test_search_short_query_returns_empty(db_session):
    assert await search_users(db_session, "") == []
    assert await search_users(db_session, "q") == []


async def test_search_returns_payload_fields(db_session):
    await _make_user(db_session, "quantum075", "1001")

    results = await search_users(db_session, "quantum075")

    assert results[0] == {
        "login": "quantum075",
        "twitch_id": "1001",
        "avatar_url": "https://example.com/quantum075.png",
    }


# ----------------------------------------------------------------------
# Роут /api/user/search
# ----------------------------------------------------------------------


async def test_search_requires_auth(client):
    resp = await client.get("/api/user/search", params={"q": "quantum"})
    assert resp.status_code in (401, 403)


async def test_search_short_query_rejected(client, user_auth_mock, test_user):  # noqa: ARG001
    resp = await client.get("/api/user/search", params={"q": "q"})
    assert resp.status_code == 422


async def test_search_endpoint(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    await _make_user(db_session, "quantum075", "1001")

    resp = await client.get("/api/user/search", params={"q": "kwantum075"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["chatters"] == []
    assert [u["login"] for u in data["users"]] == ["quantum075"]
    assert data["users"][0]["source"] == "user"


async def test_search_context_bans_excludes_staff_bots_and_self(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    # quantum075 — стафф-админ, quantum075bot — известный бот, quantum_fan — обычный зритель
    await _make_user(db_session, "quantum075", "2001")
    await _make_user(db_session, "quantum075bot", "2002")
    await _make_user(db_session, "quantum_fan", "2003")
    await _make_user(db_session, "test_user_fan", "2004")

    resp = await client.get("/api/user/search", params={"q": "quantum", "context": "bans"})

    assert resp.status_code == 200
    assert [u["login"] for u in resp.json()["users"]] == ["quantum_fan"]

    # Сам залогиненный (test_user) не должен попадать в подсказки для бана
    resp = await client.get("/api/user/search", params={"q": "test", "context": "bans"})
    assert [u["login"] for u in resp.json()["users"]] == ["test_user_fan"]


async def test_search_context_deposit_keeps_staff(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    await _make_user(db_session, "quantum075", "2001")

    resp = await client.get("/api/user/search", params={"q": "quantum075", "context": "deposit"})

    assert resp.status_code == 200
    assert [u["login"] for u in resp.json()["users"]] == ["quantum075"]


# ----------------------------------------------------------------------
# Чаттеры канала в подсказках (context=bans)
# ----------------------------------------------------------------------


async def test_search_bans_context_merges_chatters(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    # vasya_pupkin — и пользователь сайта, и активный зритель: в чаттерах дедупится
    await _make_user(db_session, "vasya_pupkin", "3001")
    fake_bot = FakeChatBot(
        [
            ("Vasya_Pupkin", "5 мин назад"),
            ("VasyaViewer", "1 мин назад"),
            ("totally_unrelated", "30 мин назад"),
        ]
    )

    with chat_bot_override(fake_bot):
        resp = await client.get("/api/user/search", params={"q": "vas", "context": "bans"})

    assert resp.status_code == 200
    data = resp.json()
    assert [u["login"] for u in data["users"]] == ["vasya_pupkin"]
    # totally_unrelated не совпал по запросу «vas» ни подстрокой, ни нечётко
    assert [c["name"] for c in data["chatters"]] == ["VasyaViewer"]
    assert data["chatters"][0]["source"] == "chatter"


async def test_search_bans_context_excludes_staff_chatters(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    fake_bot = FakeChatBot(
        [
            ("fufigg", "2 мин назад"),  # стафф
            ("Nightbot", "1 мин назад"),  # известный бот
            ("Test_User", "1 мин назад"),  # сам банящий (регистр отличается)
            ("RegularChatter", "1 мин назад"),
        ]
    )
    with chat_bot_override(fake_bot):
        for q in ("figg", "night", "test"):
            resp = await client.get("/api/user/search", params={"q": q, "context": "bans"})
            assert resp.status_code == 200
            assert resp.json()["chatters"] == [], f"q={q}"

        resp = await client.get("/api/user/search", params={"q": "regular", "context": "bans"})
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()["chatters"]] == ["RegularChatter"]


async def test_search_generic_context_skips_chatters(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    await _make_user(db_session, "vasya_pupkin", "3001")
    fake_bot = FakeChatBot([("VasyaViewer", "1 мин назад")])

    with chat_bot_override(fake_bot):
        resp = await client.get("/api/user/search", params={"q": "vas"})

    assert resp.status_code == 200
    assert resp.json()["chatters"] == []
