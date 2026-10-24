# ruff: noqa: S101, ARG001

"""Тесты consent/boosty API (ФЗ-152): фиксация согласия с cookies и переходов на Boosty."""

from datetime import datetime


async def test_cookie_consent_requires_auth(client):
    resp = await client.post("/api/user/consent/cookie-consent")
    assert resp.status_code in (401, 403)


async def test_cookie_consent_persists_timestamp(client, user_auth_mock, test_user):
    assert test_user.cookie_consent_at is None

    resp = await client.post("/api/user/consent/cookie-consent")

    assert resp.status_code == 200
    assert test_user.cookie_consent_at is not None
    assert (datetime.utcnow() - test_user.cookie_consent_at).total_seconds() < 60


async def test_boosty_dismiss_persists_timestamp(client, user_auth_mock, test_user):
    assert test_user.boosty_dismissed_at is None

    resp = await client.post("/api/user/consent/boosty/dismiss")

    assert resp.status_code == 200
    assert test_user.boosty_dismissed_at is not None


async def test_boosty_click_increments_counter_and_sets_cooldown(client, user_auth_mock, test_user):
    test_user.boosty_clicks = 0

    for _ in range(2):
        resp = await client.post("/api/user/consent/boosty/click")
        assert resp.status_code == 200

    assert test_user.boosty_clicks == 2
    # После перехода предложение скрывается на месяц — dismissed_at тоже ставится.
    assert test_user.boosty_dismissed_at is not None
