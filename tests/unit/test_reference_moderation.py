# ruff: noqa: S101

from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.models import CharacterInfo
from utils.reference_moderation import (
    MIN_REFERENCE_FOLLOWERS,
    ensure_reference_moderator,
    ensure_reference_upload_allowed,
    is_reference_admin,
    is_reference_moderator,
)


def _user(login: str, followers: int | None = 100):
    return SimpleNamespace(login_name=login, followers_count=followers)


@pytest.mark.parametrize("login", ["quantum075", "fufigg", "BEATR1XO", "toad_anna", "anna_toad", "d_e_l_y", "fra3a"])
def test_reference_moderator_allowlist(login):
    assert is_reference_moderator(login)
    assert ensure_reference_moderator(_user(login)).login_name == login


def test_reference_moderator_rejects_other_users():
    with pytest.raises(HTTPException) as exc_info:
        ensure_reference_moderator(_user("viewer"))
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("followers", [None, 0, MIN_REFERENCE_FOLLOWERS - 1])
def test_reference_upload_requires_twenty_followers(followers):
    with pytest.raises(HTTPException) as exc_info:
        ensure_reference_upload_allowed(_user("viewer", followers))
    assert exc_info.value.status_code == 403


def test_reference_upload_allows_streamer_and_admin():
    ensure_reference_upload_allowed(_user("streamer", MIN_REFERENCE_FOLLOWERS))
    ensure_reference_upload_allowed(_user("quantum075", None))
    assert is_reference_admin("QUANTUM075")


def test_character_info_defaults_keep_existing_rows_approved():
    approved = CharacterInfo.__table__.c.approved
    assert approved.default.arg is True
    assert str(approved.server_default.arg) == "true"


def test_approved_filter_compiles_to_true():
    query = CharacterInfo.approved.is_(True)
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert sql == "character_info.approved IS true"


def test_character_default_trusts_legacy_insert():
    engine = create_engine("sqlite://")
    CharacterInfo.__table__.create(engine)
    with Session(engine) as session:
        reference = CharacterInfo(name="streamer", file_id=uuid4())
        session.add(reference)
        session.flush()
        assert session.scalar(select(CharacterInfo.approved)) is True


def test_explicit_sql_null_overrides_legacy_default_for_new_upload():
    engine = create_engine("sqlite://")
    CharacterInfo.__table__.create(engine)
    with Session(engine) as session:
        reference = CharacterInfo(name="streamer", file_id=uuid4(), approved=sa.null())  # type: ignore[arg-type]
        session.add(reference)
        session.flush()
        assert session.scalar(select(CharacterInfo.approved)) is None
