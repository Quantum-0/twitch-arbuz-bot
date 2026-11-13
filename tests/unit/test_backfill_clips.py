"""Юнит-тесты хелперов скрипта бэкфилла: парсинг дат и фильтр окна."""

from datetime import datetime

from scripts.backfill_clips import parse_dt, window_items


def test_parse_dt_formats() -> None:
    assert parse_dt("2026-11-09") == datetime(2026, 11, 9)
    assert parse_dt("2026-11-09T00:00:00Z") == datetime(2026, 11, 9)
    assert parse_dt("2026-11-09 03:00:00") == datetime(2026, 11, 9, 3, 0, 0)
    # Зона конвертируется в naive UTC.
    assert parse_dt("2026-11-09T03:00:00+03:00") == datetime(2026, 11, 9, 0, 0, 0)


def test_window_items_inclusive_bounds() -> None:
    items = [
        {"id": "old", "created_at": "2026-11-08T23:59:59Z"},
        {"id": "start", "created_at": "2026-11-09T00:00:00Z"},
        {"id": "mid", "created_at": "2026-11-09T12:00:00Z"},
        {"id": "end", "created_at": "2026-11-10T00:00:00Z"},
        {"id": "new", "created_at": "2026-11-10T00:00:01Z"},
    ]

    result = window_items(items, datetime(2026, 11, 9), datetime(2026, 11, 10))

    assert [item["id"] for item in result] == ["start", "mid", "end"]


def test_window_items_accepts_nanosecond_timestamps() -> None:
    """Get Videos отдаёт created_at с наносекундами — парсер поллера их усекает."""
    items = [{"id": "v1", "created_at": "2026-11-09T12:00:00.123456789+00:00"}]

    result = window_items(items, datetime(2026, 11, 9), datetime(2026, 11, 10))

    assert [item["id"] for item in result] == ["v1"]
