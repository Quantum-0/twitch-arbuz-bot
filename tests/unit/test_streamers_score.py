# ruff: noqa: S101

"""Скоринг /streamers: бонусы за награду мемкоинов (memealerts_deposits) и ачивки."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from database.models import MemealertsDeposit, User, UserAchievement
from schemas.enums import MemealertsDepositStatus
from utils.streamers import _build_select_query
from utils.streamers_sort import _memealerts_reward_score, compute_streamer_score


def _score(**overrides: int) -> float:
    base = {
        "deposits_count": 0,
        "deposits_users": 0,
        "deposits_top1_count": 0,
        "deposits_weekly_coins": 0,
        "deposits_weekly_users": 0,
    }
    base.update(overrides)
    return _memealerts_reward_score(base)


def test_no_deposits_no_bonus():
    assert _score() == 0.0


def test_deposits_count_tiers():
    assert _score(deposits_count=2) == 0.0
    assert _score(deposits_count=3) == pytest.approx(0.1)
    assert _score(deposits_count=20) == pytest.approx(0.1)
    assert _score(deposits_count=21) == pytest.approx(0.2)


def test_unique_users_tiers():
    # Граница 10 трактуется в пользу верхнего тира (+0.2).
    assert _score(deposits_users=2) == 0.0
    assert _score(deposits_users=3) == pytest.approx(0.1)
    assert _score(deposits_users=9) == pytest.approx(0.1)
    assert _score(deposits_users=10) == pytest.approx(0.2)


def test_weekly_coins_tiers():
    # Ровно 100 мемкоинов за неделю — ветка «слишком дешёвая» (+0.1).
    assert _score(deposits_weekly_coins=0) == 0.0
    assert _score(deposits_weekly_coins=50) == pytest.approx(0.2)
    assert _score(deposits_weekly_coins=99) == pytest.approx(0.2)
    assert _score(deposits_weekly_coins=100) == pytest.approx(0.1)
    assert _score(deposits_weekly_coins=150) == pytest.approx(0.1)


def test_weekly_unique_users():
    assert _score(deposits_weekly_users=2) == 0.0
    assert _score(deposits_weekly_users=3) == pytest.approx(0.1)


def test_anti_freeload_only_above_half():
    # Штраф только когда один зритель сделал БОЛЬШЕ половины (>=10 пополнений).
    assert _score(deposits_count=10, deposits_top1_count=6) == pytest.approx(0.1 - 0.1)
    assert _score(deposits_count=10, deposits_top1_count=5) == pytest.approx(0.1)  # ровно половина
    assert _score(deposits_count=5, deposits_top1_count=5) == pytest.approx(0.1)  # <10 — не применяется


def test_compute_streamer_score_tolerates_missing_deposit_fields(monkeypatch):
    # Словари без deposit-агрегатов (старые вызовы/тесты) не должны падать.
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

    without_deposits = compute_streamer_score(dict(base))
    with_deposits = compute_streamer_score({**base, "deposits_count": 25, "deposits_users": 12})
    assert with_deposits == pytest.approx(without_deposits + 0.4)


def test_achievements_count_adds_005_each(monkeypatch):
    # +0.05 за каждую полученную ачивку; без поля (старые словари) — как с нулём.
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

    without = compute_streamer_score(dict(base))
    assert compute_streamer_score({**base, "achievements_count": 0}) == pytest.approx(without)
    assert compute_streamer_score({**base, "achievements_count": 1}) == pytest.approx(without + 0.05)
    assert compute_streamer_score({**base, "achievements_count": 7}) == pytest.approx(without + 0.35)


@pytest.mark.asyncio
async def test_streamers_query_counts_achievements(db_session, test_user):
    test_user.followers_count = 10  # иначе where(followers_count > 2) выкинет юзера
    db_session.add(UserAchievement(twitch_id=test_user.twitch_id, code="easter_dots", level=1))
    db_session.add(UserAchievement(twitch_id=test_user.twitch_id, code="likes_given", level=3))
    await db_session.commit()

    stmt = _build_select_query().where(User.login_name == test_user.login_name)
    row = (await db_session.execute(stmt)).one()._asdict()

    # Строка в user_achievements = полученная ачивка (уровень не важен).
    assert row["achievements_count"] == 2


def test_streamers_query_aggregates_deposits_in_sql():
    sql = str(_build_select_query().compile(compile_kwargs={"literal_binds": True}))

    assert "memealerts_deposits" in sql
    for column in ("deposits_count", "deposits_users", "deposits_top1_count", "deposits_weekly_coins"):
        assert column in sql


@pytest.mark.asyncio
async def test_streamers_query_aggregates_real_deposits(db_session, test_user):
    test_user.followers_count = 10  # иначе where(followers_count > 2) выкинет юзера
    now = datetime.now(UTC).replace(tzinfo=None)

    def add(chatter: str, *, coins: int, days_ago: int, status: MemealertsDepositStatus) -> None:
        db_session.add(
            MemealertsDeposit(
                twitch_id=test_user.twitch_id,
                redemption_id=uuid4(),
                chatter_twitch_id=chatter,
                chatter_login=f"viewer_{chatter}",
                memealerts_username=f"ma_{chatter}",
                memecoins=coins,
                status=status,
                created_at=now - timedelta(days=days_ago),
            )
        )

    # Окна различаются только между 7 и 90 днями — проверяем и эту границу:
    # 10-дневная давность попадает в 90д-агрегаты, но НЕ в недельные.
    # Плюс провальный и древний успешный — исключаются из всех агрегатов.
    add("111", coins=2, days_ago=1, status=MemealertsDepositStatus.SUCCEEDED)
    add("111", coins=2, days_ago=2, status=MemealertsDepositStatus.SUCCEEDED)
    add("222", coins=2, days_ago=3, status=MemealertsDepositStatus.SUCCEEDED)
    add("444", coins=5, days_ago=10, status=MemealertsDepositStatus.SUCCEEDED)
    add("333", coins=2, days_ago=1, status=MemealertsDepositStatus.FAILED)
    add("111", coins=2, days_ago=100, status=MemealertsDepositStatus.SUCCEEDED)
    await db_session.commit()

    stmt = _build_select_query().where(User.login_name == test_user.login_name)
    row = (await db_session.execute(stmt)).one()._asdict()

    assert row["deposits_count"] == 4
    assert row["deposits_users"] == 3
    assert row["deposits_top1_count"] == 2
    assert row["deposits_weekly_coins"] == 6  # 10-дневняя покупка не в недельном окне
    assert row["deposits_weekly_users"] == 2


@pytest.mark.asyncio
async def test_get_streamers_list_applies_deposit_score(db_session, test_user, monkeypatch):
    """Сквозная цепочка: SQL-агрегаты → row._asdict() → compute_streamer_score.

    Два одинаковых стримера (одинаковые created_at/interacted_at, random
    зафиксирован): у одного 4 успешных пополнения от 3 зрителей за 10 дней —
    бонус ровно +0.2 (3–20 пополнений +0.1, 3–9 уникальных +0.1, недельных нет).
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from utils.streamers import get_streamers_list

    monkeypatch.setattr("utils.streamers_sort.random.random", lambda: 0)
    long_ago = datetime.now() - timedelta(days=60)

    other = User(
        twitch_id="999999",
        login_name="other_streamer",
        profile_image_url="https://example.com/o.png",
        _access_token="t",
        _refresh_token="t",
        followers_count=10,
        created_at=long_ago,
        interacted_at=long_ago,
    )
    test_user.followers_count = 10
    test_user.created_at = long_ago
    test_user.interacted_at = long_ago
    db_session.add(other)
    await db_session.commit()

    now = datetime.now(UTC).replace(tzinfo=None)
    for i, chatter in enumerate(("111", "111", "222", "444")):
        db_session.add(
            MemealertsDeposit(
                twitch_id=test_user.twitch_id,
                redemption_id=uuid4(),
                chatter_twitch_id=chatter,
                chatter_login=f"viewer_{chatter}_{i}",
                memealerts_username=f"ma_{chatter}",
                memecoins=2,
                status=MemealertsDepositStatus.SUCCEEDED,
                created_at=now - timedelta(days=10),
            )
        )
    await db_session.commit()

    # Непустой сет онлайн-стримов из кэша — Twitch-клиент не вызывается.
    cache = SimpleNamespace(get_set=AsyncMock(return_value={"nobody_online"}), set_set=AsyncMock())
    rows = await get_streamers_list(db_session, object(), cache, sort="recommended")

    scores = {row["username"]: row["score"] for row in rows}
    assert scores["other_streamer"] == pytest.approx(scores[test_user.login_name] - 0.2)
