# ruff: noqa: S101

from datetime import datetime
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from database.models import GeneratedImage
from utils.galleries import build_public_references_query
from utils.stickers_query import build_public_stickers_query, serialize_sticker_rows


def test_public_stickers_query_enforces_channel_privacy():
    query = build_public_stickers_query(before=datetime(2026, 9, 1), limit=20)
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "JOIN twitch_bot_users" in sql
    assert "JOIN twitch_user_settings" in sql
    assert "twitch_user_settings.ai_stickers_show_in_profile IS true" in sql
    assert "generated_image.created_at < '2026-09-01 00:00:00'" in sql
    assert "LIMIT 21" in sql


def test_serialize_public_stickers_returns_cursor_only_when_more_items_exist():
    first = GeneratedImage(
        file_id=uuid4(),
        prompt="first",
        by_chatter="viewer",
        on_channel=1,
        created_at=datetime(2026, 9, 2),
    )
    second = GeneratedImage(
        file_id=uuid4(),
        prompt="second",
        by_chatter="viewer",
        on_channel=1,
        created_at=datetime(2026, 9, 1),
    )

    result = serialize_sticker_rows([(first, "channel"), (second, "channel")], limit=1)

    assert len(result["items"]) == 1
    assert result["items"][0]["channel_login"] == "channel"
    assert result["next_cursor"] == first.created_at.isoformat()


def test_public_references_query_enforces_owner_privacy_and_content():
    sql = str(
        build_public_references_query().compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "JOIN twitch_bot_users" in sql
    assert "JOIN twitch_user_settings" in sql
    assert "twitch_user_settings.ai_reference_show_in_profile IS true" in sql
    assert "character_info.file_id IS NOT NULL OR character_info.description IS NOT NULL" in sql
