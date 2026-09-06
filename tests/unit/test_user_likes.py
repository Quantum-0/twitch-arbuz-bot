# ruff: noqa: S101, S106

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from database.models import User, UserLike
from routers.api.user.likes import like_user, unlike_user
from utils.streamers import _apply_sort, _build_select_query
from utils.streamers_sort import compute_streamer_score


def _user(user_id: int, login: str) -> User:
    return User(
        id=user_id,
        twitch_id=str(user_id),
        login_name=login,
        profile_image_url="https://example.com/avatar.png",
        _access_token="encrypted-access-token",
        _refresh_token="encrypted-refresh-token",
    )


def test_user_like_model_has_integrity_and_lookup_indexes():
    table = UserLike.__table__
    index_names = {index.name for index in table.indexes}

    assert "uq_user_likes_from_to" in index_names
    assert "ix_user_likes_from_user_id" in index_names
    assert "ix_user_likes_to_user_id" in index_names
    assert {fk.target_fullname for fk in table.foreign_keys} == {
        "twitch_bot_users.id",
    }
    assert any(constraint.name == "ck_user_likes_not_self" for constraint in table.constraints)


def test_streamers_query_aggregates_likes_in_sql():
    sql = str(_build_select_query().compile(compile_kwargs={"literal_binds": True}))

    assert "count(user_likes.id)" in sql
    assert "LEFT OUTER JOIN" in sql
    assert "likes_count" in sql


def test_sort_by_likes():
    rows = [
        {"username": "charlie", "likes_count": 2},
        {"username": "alice", "likes_count": 5},
        {"username": "bob", "likes_count": 5},
    ]

    assert [row["username"] for row in _apply_sort(rows, "likes", "desc")] == ["alice", "bob", "charlie"]


def test_recommended_score_like_boost_is_capped(monkeypatch):
    monkeypatch.setattr("utils.streamers_sort.random.random", lambda: 0)
    base = {
        "is_live": False,
        "created_at": datetime.now() - timedelta(days=60),
        "interacted_at": datetime.now() - timedelta(days=60),
        "followers": 0,
        "username": "some_streamer",
        "donated": 0,
        "is_beta_tester": False,
        "memealerts_enabled": False,
        "chat_bot_enabled": False,
        "ai_stickers_enabled": False,
    }

    without_likes = compute_streamer_score({**base, "likes_count": 0})
    assert compute_streamer_score({**base, "likes_count": 10}) == pytest.approx(without_likes + 0.5)
    assert compute_streamer_score({**base, "likes_count": 100}) == pytest.approx(without_likes + 2)


@pytest.mark.asyncio
async def test_like_is_idempotent_and_returns_current_count():
    actor = _user(1, "actor")
    target = _user(2, "target")
    db = AsyncMock()
    db.get.return_value = target
    db.scalar.return_value = 7

    result = await like_user(target.id, db, actor)

    assert result == {"liked": True, "likes_count": 7}
    statement = db.execute.await_args_list[0].args[0]
    assert "ON CONFLICT" in str(statement)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_unlike_is_idempotent_and_returns_current_count():
    actor = _user(1, "actor")
    target = _user(2, "target")
    db = AsyncMock()
    db.get.return_value = target
    db.scalar.return_value = 0

    result = await unlike_user(target.id, db, actor)

    assert result == {"liked": False, "likes_count": 0}
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_like_rejects_self_like_before_database_access():
    actor = _user(1, "actor")
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await like_user(actor.id, db, actor)

    assert exc_info.value.status_code == 400
    db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_like_returns_404_for_unknown_target():
    actor = _user(1, "actor")
    db = AsyncMock()
    db.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await like_user(999, db, actor)

    assert exc_info.value.status_code == 404
