# ruff: noqa: S101

"""Тесты ачивок: реестр, сервис (unlock / ленивое вычисление / заморозка), API, условие трусов."""

import inspect
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa

from database.models import BalanceOperation, MemealertsDeposit, TTSSettings, UserAchievement
from main import app
from routers.api.user import achievements as achievements_module
from schemas.enums import (
    BalanceOperationAction,
    BalanceOperationType,
    MemealertsDepositStatus,
)
from services.achievements import AchievementsService, AchievementView
from services.cache import Cache
from tests.unit.fixtures.fake_redis import FakeRedis, SessionCtx
from twitch.chat.commands.pants import pants_achievement_earned
from utils.achievements import ACHIEVEMENTS, level_for, to_roman


def make_service(db_session) -> AchievementsService:
    return AchievementsService(lambda: SessionCtx(db_session))


async def _rows(db_session, twitch_id: str) -> dict[str, UserAchievement]:
    result = await db_session.execute(sa.select(UserAchievement).where(UserAchievement.twitch_id == twitch_id))
    return {row.code: row for row in result.scalars().all()}


def _dep(func, param: str):
    """Достать Depends-объект (в т.ч. Provide[...]) из аннотации параметра роута."""
    return inspect.signature(func).parameters[param].annotation.__metadata__[0].dependency


@contextmanager
def _override(dep, factory):
    app.dependency_overrides[dep] = factory
    try:
        yield
    finally:
        app.dependency_overrides.pop(dep, None)


# ----------------------------------------------------------------------
# Реестр
# ----------------------------------------------------------------------


def test_registry_icons_exist_and_thresholds_sorted():
    static_images = Path(__file__).parents[2] / "static" / "images"
    assert len(ACHIEVEMENTS) == 14
    for def_ in ACHIEVEMENTS.values():
        assert (static_images / def_.icon).is_file(), def_.icon
        assert def_.thresholds == tuple(sorted(def_.thresholds))


def test_level_for_and_to_roman():
    assert level_for((), 0) == 0
    assert level_for((), 1) == 1
    assert level_for((10, 25, 100), 9) == 0
    assert level_for((10, 25, 100), 10) == 1
    assert level_for((10, 25, 100), 30) == 2
    assert level_for((10, 25, 100), 1000) == 3
    assert to_roman(1) == "I"
    assert to_roman(3) == "III"
    assert to_roman(4) == "IV"


# ----------------------------------------------------------------------
# Сервис: unlock
# ----------------------------------------------------------------------


async def test_unlock_idempotent(db_session, test_user):
    service = make_service(db_session)

    assert await service.unlock(test_user.twitch_id, "easter_dots") is True
    assert await service.unlock(test_user.twitch_id, "easter_dots") is False

    rows = await _rows(db_session, test_user.twitch_id)
    assert list(rows) == ["easter_dots"]
    assert rows["easter_dots"].level == 1


async def test_unlock_unknown_code(db_session, test_user):
    service = make_service(db_session)

    assert await service.unlock(test_user.twitch_id, "no_such_code") is False
    assert await _rows(db_session, test_user.twitch_id) == {}


# ----------------------------------------------------------------------
# Сервис: ленивое вычисление
# ----------------------------------------------------------------------


async def test_evaluate_likes_levels_and_monotonic(db_session, test_user):
    service = make_service(db_session)

    # 4 лайка — порог не достигнут, строки нет
    assert await service.evaluate_and_get(test_user, likes_given=4, likes_received=0) == []

    views = await service.evaluate_and_get(test_user, likes_given=5, likes_received=0)
    assert [v.code for v in views] == ["likes_given"]
    assert views[0].level == 1
    assert views[0].roman == "I"

    views = await service.evaluate_and_get(test_user, likes_given=25, likes_received=0)
    assert views[0].level == 3
    assert views[0].roman == "III"

    # Зрители сняли лайки — ачивка не понижается и не пропадает
    views = await service.evaluate_and_get(test_user, likes_given=3, likes_received=0)
    assert [v.code for v in views] == ["likes_given"]
    assert views[0].level == 3


async def test_evaluate_memecoins_counts_only_succeeded(db_session, test_user):
    for i in range(13):
        db_session.add(
            MemealertsDeposit(
                twitch_id=test_user.twitch_id,
                redemption_id=uuid4(),
                chatter_twitch_id=str(1000 + i),
                chatter_login=f"viewer{i}",
                memealerts_username=f"viewer{i}",
                points_spent=100,
                memecoins=2,
                status=MemealertsDepositStatus.SUCCEEDED if i < 10 else MemealertsDepositStatus.FAILED,
            )
        )
    await db_session.commit()

    views = await make_service(db_session).evaluate_and_get(test_user, likes_given=0, likes_received=0)
    by_code = {v.code: v for v in views}
    assert by_code["memecoins_sold"].level == 1  # 10 succeeded, 3 failed не считаются


async def test_evaluate_ai_stickers_counts_spends_only(db_session, test_user):
    for _ in range(2):
        db_session.add(
            BalanceOperation(
                twitch_id=test_user.twitch_id,
                type=BalanceOperationType.SPEND,
                action=BalanceOperationAction.AI_STICKER,
                amount=Decimal("1.00"),
                balance_after=Decimal("9.00"),
            )
        )
    db_session.add(
        BalanceOperation(
            twitch_id=test_user.twitch_id,
            type=BalanceOperationType.DEPOSIT,
            action=BalanceOperationAction.ADMIN_DEPOSIT,
            amount=Decimal("10.00"),
            balance_after=Decimal("10.00"),
        )
    )
    await db_session.commit()

    views = await make_service(db_session).evaluate_and_get(test_user, likes_given=0, likes_received=0)
    by_code = {v.code: v for v in views}
    assert by_code["ai_stickers_used"].level == 1


async def test_evaluate_state_flags_and_all_rewards(db_session, test_user):
    test_user.donated = 100
    test_user.in_beta_test = True
    test_user.created_at = datetime.now() - timedelta(days=400)
    test_user.overlays_last_usage = datetime.now() - timedelta(days=1)
    test_user.links.telegram = "tg"
    test_user.links.youtube = "c/channel"
    test_user.links.tiktok = "tok"
    test_user.links.discord = "https://discord.gg/abc"
    test_user.settings.ai_sticker_reward_id = uuid4()
    test_user.memealerts.memealerts_reward = uuid4()
    db_session.add(TTSSettings(user_id=test_user.id, tts_reward_id=uuid4()))
    await db_session.commit()

    views = await make_service(db_session).evaluate_and_get(test_user, likes_given=0, likes_received=0)
    assert {"donated", "beta_tester", "veteran", "overlay_used", "social_links", "all_rewards"} <= {
        v.code for v in views
    }
    # одноуровневые ачивки — без римской цифры
    for view in views:
        if view.code == "donated":
            assert view.roman is None


async def test_evaluate_missing_links_reward_no_achievements(db_session, test_user):
    # Ничего не заполнено — ни одной ачивки, ни одной строки
    views = await make_service(db_session).evaluate_and_get(test_user, likes_given=0, likes_received=0)
    assert views == []
    assert await _rows(db_session, test_user.twitch_id) == {}


async def test_frozen_single_level_survives_source_removal(db_session, test_user):
    test_user.links.telegram = "tg"
    test_user.links.youtube = "c/channel"
    test_user.links.tiktok = "tok"
    test_user.links.discord = "https://discord.gg/abc"
    await db_session.commit()

    service = make_service(db_session)
    views = await service.evaluate_and_get(test_user, likes_given=0, likes_received=0)
    assert "social_links" in {v.code for v in views}

    test_user.links.telegram = None
    test_user.links.youtube = None
    test_user.links.tiktok = None
    test_user.links.discord = None
    await db_session.commit()

    views = await service.evaluate_and_get(test_user, likes_given=0, likes_received=0)
    assert "social_links" in {v.code for v in views}


async def test_frozen_max_level_source_not_rechecked(db_session, test_user):
    # Максимальный уровень уже записан — источник (депозиты) можно не смотреть
    db_session.add(UserAchievement(twitch_id=test_user.twitch_id, code="memecoins_sold", level=3))
    await db_session.commit()

    service = make_service(db_session)
    # Шпион на тяжёлых источниках: заморозка обязана исключить memecoins_sold
    # из запрошенных кодов (иначе тест доказывал бы только монотонность).
    queried_codes: list[list[str]] = []
    original = service._query_values  # noqa: SLF001

    async def spy(session, user, codes):  # type: ignore[no-untyped-def]
        queried_codes.append(list(codes))
        return await original(session, user, codes)

    service._query_values = spy  # type: ignore[method-assign]  # noqa: SLF001

    views = await service.evaluate_and_get(test_user, likes_given=0, likes_received=0)
    by_code = {v.code: v for v in views}
    assert by_code["memecoins_sold"].level == 3
    assert by_code["memecoins_sold"].roman == "III"
    assert "memecoins_sold" not in [code for batch in queried_codes for code in batch]


async def test_frozen_single_level_source_not_queried_again(db_session, test_user):
    # Получаем social_links, затем шпионим за тяжёлыми источниками при
    # повторном просмотре: одноуровневая ачивка не должна запрашиваться.
    test_user.links.telegram = "tg"
    test_user.links.youtube = "c/channel"
    test_user.links.tiktok = "tok"
    test_user.links.discord = "https://discord.gg/abc"
    await db_session.commit()

    service = make_service(db_session)
    views = await service.evaluate_and_get(test_user, likes_given=0, likes_received=0)
    assert "social_links" in {v.code for v in views}

    queried_codes: list[list[str]] = []

    async def spy(session, user, codes):  # type: ignore[no-untyped-def]  # noqa: ARG001
        queried_codes.append(list(codes))
        return {}

    service._query_values = spy  # type: ignore[method-assign]  # noqa: SLF001

    views = await service.evaluate_and_get(test_user, likes_given=0, likes_received=0)
    assert "social_links" in {v.code for v in views}
    assert "social_links" not in [code for batch in queried_codes for code in batch]


async def test_newest_achievement_first(db_session, test_user):
    service = make_service(db_session)
    await service.evaluate_and_get(test_user, likes_given=5, likes_received=0)
    await service.unlock(test_user.twitch_id, "star_torn")

    views = await service.evaluate_and_get(test_user, likes_given=5, likes_received=0)
    assert views[0].code == "star_torn"
    assert {v.code for v in views} == {"likes_given", "star_torn"}


async def test_stale_db_code_hidden_not_fatal(db_session, test_user):
    db_session.add(UserAchievement(twitch_id=test_user.twitch_id, code="deleted_code", level=1))
    await db_session.commit()

    views = await make_service(db_session).evaluate_and_get(test_user, likes_given=5, likes_received=0)
    assert {v.code for v in views} == {"likes_given"}


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------


async def test_easter_dots_api_requires_auth(client):
    resp = await client.post("/api/user/achievements/easter-dots")
    assert resp.status_code in (401, 403)


async def test_easter_dots_api_idempotent(client, db_session, test_user, user_auth_mock):  # noqa: ARG001
    service = make_service(db_session)
    dep = _dep(achievements_module.easter_dots, "achievements")
    with _override(dep, lambda: service):
        assert (await client.post("/api/user/achievements/easter-dots")).status_code == 200
        assert (await client.post("/api/user/achievements/easter-dots")).status_code == 200

    rows = await _rows(db_session, test_user.twitch_id)
    assert list(rows) == ["easter_dots"]


async def test_star_break_api_flow(client, db_session, test_user):
    from utils.overlay_secret import ensure_overlay_secret

    service = make_service(db_session)
    cache = Cache(redis=FakeRedis(), binary_redis=FakeRedis())
    secret = str(await ensure_overlay_secret(db_session, test_user))
    channel_id = int(test_user.twitch_id)

    dep_cache = _dep(achievements_module.star_break, "cache")
    dep_service = _dep(achievements_module.star_break, "achievements")
    with _override(dep_cache, lambda: cache), _override(dep_service, lambda: service):
        # без заголовка
        resp = await client.post("/api/user/achievements/star-break", json={"channel_id": channel_id})
        assert resp.status_code == 401
        # неверный секрет
        resp = await client.post(
            "/api/user/achievements/star-break",
            json={"channel_id": channel_id},
            headers={"X-Overlay-Secret": str(UUID(int=0))},
        )
        assert resp.status_code == 403
        # несуществующий канал
        resp = await client.post(
            "/api/user/achievements/star-break",
            json={"channel_id": 999999999},
            headers={"X-Overlay-Secret": secret},
        )
        assert resp.status_code == 404
        # валидный репорт
        resp = await client.post(
            "/api/user/achievements/star-break",
            json={"channel_id": channel_id},
            headers={"X-Overlay-Secret": secret},
        )
        assert resp.status_code == 200
        # повтор в пределах окна — rate-limited, второй строки не появляется
        resp = await client.post(
            "/api/user/achievements/star-break",
            json={"channel_id": channel_id},
            headers={"X-Overlay-Secret": secret},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "rate_limited"}

    rows = await _rows(db_session, test_user.twitch_id)
    assert list(rows) == ["star_torn"]


# ----------------------------------------------------------------------
# Условие ачивки трусов
# ----------------------------------------------------------------------


def test_pants_achievement_earned():
    assert pants_achievement_earned("streamer", "streamer", "streamer", 5)
    assert pants_achievement_earned("Streamer", "STREAMER", "streamer", 7)
    assert not pants_achievement_earned("streamer", "streamer", "streamer", 4)  # мало участников
    assert not pants_achievement_earned("streamer", "viewer", "viewer", 10)  # цель не стример
    assert not pants_achievement_earned("streamer", "streamer", "viewer", 10)  # выиграл зритель


# ----------------------------------------------------------------------
# AchievementView: поля для шаблона
# ----------------------------------------------------------------------


def test_achievement_view_fields():
    view = AchievementView(
        code="memecoins_sold",
        name="Мем-магнат",
        icon="achievements/memecoins_sold.svg",
        level=2,
        roman="II",
        unlocked_at=datetime(2026, 11, 15),
    )
    assert view.roman == "II"
