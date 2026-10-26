# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты services/memes.py (MemealertsService): бонус, поиск саппортеров, кэш в БД."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from memealerts.types.exceptions import MAError, MAUserNotFoundError
from memealerts.types.models import Supporter
from memealerts.types.user_id import UserID
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound

import services.memes as memes_mod
from database.models import MemealertsSupporters
from exceptions import MADuplicateUserError, MATokenInvalidError
from services.memes import MemealertsService
from tests.unit.fixtures.fake_redis import SessionCtx

SID = "a" * 24
SID2 = "b" * 24
SID3 = "c" * 24


def make_supporter(sid: str = SID, name: str = "nick", link: str | None = None) -> Supporter:
    return Supporter.model_validate(
        {
            "supporterId": sid,
            "supporterName": name,
            "supporterLink": link,
            "balance": 0,
            "spent": 0,
            "purchased": 0,
            "mutedByStreamer": False,
            "mutes": [],
            "joined": None,
        }
    )


class FakeMAClient:
    """Фейк MemealertsAsyncClient: скриптуемые ответы + запись вызовов."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.bonus_calls: list[UserID] = []
        self.supporter_pages: dict[int, Any] = {}
        self.targeted_pages: dict[str, Any] = {}
        self.full_page: Any = None
        self.find_user_result: Any = None
        self.find_user_exc: Exception | None = None
        self.give_bonus_exc: Exception | None = None
        self.get_current_result: Any = None
        self.get_current_exc: Exception | None = None
        self.calls: list[str] = []

    async def __aenter__(self) -> "FakeMAClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def give_bonus(self, user_id: UserID, amount: int) -> None:
        self.calls.append(f"give_bonus:{user_id.root}:{amount}")
        if self.give_bonus_exc:
            raise self.give_bonus_exc

    async def get_current(self) -> Any:
        self.calls.append("get_current")
        if self.get_current_exc:
            raise self.get_current_exc
        return self.get_current_result

    async def enable_stickers(self) -> bool:
        self.calls.append("enable_stickers")
        return True

    async def enable_welcome_bonus(self) -> bool:
        self.calls.append("enable_welcome_bonus")
        return True

    async def get_supporters(self, limit: int = 100, skip: int = 0, query: str | None = None) -> Any:
        self.calls.append(f"get_supporters:{skip}:{query}")
        if query is not None:
            page = self.targeted_pages.get(query, SimplePage([], total=0))
        else:
            page = self.supporter_pages.get(skip, self.full_page or SimplePage([], total=0))
        return page

    async def find_user(self, username: str) -> Any:
        self.calls.append(f"find_user:{username}")
        if self.find_user_exc:
            raise self.find_user_exc
        return self.find_user_result


class SimplePage:
    def __init__(self, data: list[Supporter], total: int):
        self.data = data
        self.total = total


@pytest.fixture
def fake_ma(monkeypatch) -> FakeMAClient:
    cli = FakeMAClient()
    monkeypatch.setattr(memes_mod, "MemealertsAsyncClient", lambda token=None: cli)
    return cli


@pytest.fixture
def svc(db_session) -> MemealertsService:
    return MemealertsService(db_session_factory=lambda: SessionCtx(db_session))


async def drain_background(svc: MemealertsService) -> None:
    if svc._background_tasks:
        await asyncio.gather(*svc._background_tasks)


class TestSimpleWrappers:
    async def test_get_current(self, fake_ma):
        fake_ma.get_current_result = "me"
        assert await MemealertsService(db_session_factory=None).get_current("tok") == "me"

    async def test_enable_stickers(self, fake_ma):
        assert await MemealertsService(db_session_factory=None).enable_stickers("tok") is True
        assert fake_ma.calls == ["enable_stickers"]

    async def test_is_id(self, svc):
        assert svc.is_id(SID) is True
        assert svc.is_id("short") is False
        assert svc.is_id("Z" * 24) is False  # не hex


class TestGiveBonus:
    async def test_success_via_supporters(self, fake_ma, svc):
        cli = fake_ma
        cli.targeted_pages["nick"] = SimplePage([make_supporter()], total=1)
        result = await svc.give_bonus("tok", "streamer", "nick", 50)
        assert result is True
        assert cli.calls == ["get_supporters:0:nick", "give_bonus:" + "a" * 24 + ":50"]
        await drain_background(svc)

    async def test_token_invalid(self, fake_ma, svc):
        cli = fake_ma
        cli.targeted_pages["nick"] = SimplePage([make_supporter()], total=1)
        cli.give_bonus_exc = MAError("api fail")
        cli.get_current_exc = MAError("401")
        with pytest.raises(MATokenInvalidError):
            await svc.give_bonus("tok", "streamer", "nick", 50)

    async def test_ma_error_with_valid_token_reraised(self, fake_ma, svc):
        cli = fake_ma
        cli.targeted_pages["nick"] = SimplePage([make_supporter()], total=1)
        cli.give_bonus_exc = MAError("supporter not found")
        cli.get_current_result = object()  # get_current успешен → токен живой
        with pytest.raises(MAError):
            await svc.give_bonus("tok", "streamer", "nick", 50)


class TestFindAndGiveBonus:
    async def test_by_direct_id(self, svc):
        cli = FakeMAClient()
        assert await svc.find_and_give_bonus(cli, SID, 10) is True
        assert cli.calls == ["give_bonus:" + "a" * 24 + ":10"]

    async def test_username_trimmed_and_lowered(self, svc):
        cli = FakeMAClient()
        cli.targeted_pages["nick"] = SimplePage([make_supporter()], total=1)
        assert await svc.find_and_give_bonus(cli, "  NiCk ", 10) is True
        assert cli.calls[0] == "get_supporters:0:nick"

    async def test_from_db_cache(self, svc, db_session):
        db_session.add(MemealertsSupporters(id=SID2, name="cached", link="cached_link"))
        await db_session.commit()
        cli = FakeMAClient()
        assert await svc.find_and_give_bonus(cli, "cached", 10) is True
        # Без единого API-вызова
        assert cli.calls == ["give_bonus:" + "b" * 24 + ":10"]

    async def test_from_db_cache_by_link(self, svc, db_session):
        db_session.add(MemealertsSupporters(id=SID3, name="other", link="tg_user"))
        await db_session.commit()
        cli = FakeMAClient()
        assert await svc.find_and_give_bonus(cli, "tg_user", 10) is True
        assert cli.calls == ["give_bonus:" + "c" * 24 + ":10"]

    async def test_multiple_db_rows_fall_to_api(self, svc, monkeypatch, db_session):
        async def raise_multiple(username: str) -> None:
            raise MultipleResultsFound()

        monkeypatch.setattr(svc, "search_supporter_from_db", raise_multiple)
        cli = FakeMAClient()
        cli.targeted_pages["nick"] = SimplePage([make_supporter()], total=1)
        assert await svc.find_and_give_bonus(cli, "nick", 10) is True
        assert "get_supporters:0:nick" in cli.calls
        await drain_background(svc)

    async def test_db_error_falls_to_api(self, svc, monkeypatch):
        async def raise_db(username: str) -> None:
            raise RuntimeError("db down")

        monkeypatch.setattr(svc, "search_supporter_from_db", raise_db)
        cli = FakeMAClient()
        cli.targeted_pages["nick"] = SimplePage([make_supporter()], total=1)
        assert await svc.find_and_give_bonus(cli, "nick", 10) is True
        await drain_background(svc)

    async def test_targeted_search_hit(self, svc):
        cli = FakeMAClient()
        cli.targeted_pages["nick"] = SimplePage([make_supporter()], total=1)
        assert await svc.find_and_give_bonus(cli, "nick", 5) is True
        assert cli.calls == ["get_supporters:0:nick", "give_bonus:" + "a" * 24 + ":5"]

    async def test_full_scan_fallback(self, svc):
        cli = FakeMAClient()
        cli.full_page = SimplePage([make_supporter()], total=1)
        assert await svc.find_and_give_bonus(cli, "nick", 5) is True
        assert cli.calls == ["get_supporters:0:nick", "get_supporters:0:None", "give_bonus:" + "a" * 24 + ":5"]
        await drain_background(svc)

    async def test_global_search_hit(self, svc):
        cli = FakeMAClient()
        cli.find_user_result = SimpleNamespace(id=UserID(SID2))
        assert await svc.find_and_give_bonus(cli, "nick", 5) is True
        assert cli.calls[-1] == "give_bonus:" + "b" * 24 + ":5"
        await drain_background(svc)

    async def test_global_search_not_found_returns_false(self, svc):
        cli = FakeMAClient()
        cli.find_user_exc = MAUserNotFoundError("nope")
        assert await svc.find_and_give_bonus(cli, "nick", 5) is False
        await drain_background(svc)

    async def test_nothing_found_returns_false(self, svc):
        cli = FakeMAClient()
        assert await svc.find_and_give_bonus(cli, "ghost", 5) is False
        assert "find_user:ghost" in cli.calls
        await drain_background(svc)


class TestPickUserFromList:
    def test_match_by_link(self, svc):
        sup = make_supporter(name="Nick Name", link="nick")
        assert svc._pick_user_from_list([sup], "nick") is sup

    def test_same_user_link_equals_name_collision(self, svc):
        # Документация текущего поведения: link и name одного и того же саппортера
        # делят общий lookup → ложный MADuplicateUserError при link == name == username.
        sup = make_supporter(name="nick", link="nick")
        with pytest.raises(MADuplicateUserError):
            svc._pick_user_from_list([sup], "nick")

    def test_match_by_name(self, svc):
        sup = make_supporter(name="Nick")
        assert svc._pick_user_from_list([sup], "nick") is sup

    def test_no_match(self, svc):
        sup = make_supporter(name="other")
        assert svc._pick_user_from_list([sup], "nick") is None

    def test_duplicate_link_keeps_first(self, svc):
        first = make_supporter(sid=SID, name="a", link="dup")
        second = make_supporter(sid=SID2, name="b", link="dup")
        assert svc._pick_user_from_list([first, second], "other") is None

    def test_duplicate_link_matching_username_raises(self, svc):
        first = make_supporter(sid=SID, name="a", link="dup")
        second = make_supporter(sid=SID2, name="b", link="dup")
        with pytest.raises(MADuplicateUserError):
            svc._pick_user_from_list([first, second], "dup")

    def test_duplicate_name_matching_username_raises(self, svc):
        first = make_supporter(sid=SID, name="dup")
        second = make_supporter(sid=SID2, name="dup")
        with pytest.raises(MADuplicateUserError):
            svc._pick_user_from_list([first, second], "dup")

    def test_empty(self, svc):
        assert svc._pick_user_from_list([], "nick") is None


class TestFindUserInSupporters:
    async def test_targeted_hit_no_full_scan(self, svc):
        cli = FakeMAClient()
        cli.targeted_pages["nick"] = SimplePage([make_supporter()], total=1)
        result = await svc.find_user_in_supporters(cli, "nick")
        assert result is not None
        assert result.supporter_id == UserID(SID)
        await drain_background(svc)

    async def test_fallback_full_scan(self, svc):
        cli = FakeMAClient()
        cli.full_page = SimplePage([make_supporter()], total=1)
        result = await svc.find_user_in_supporters(cli, "nick")
        assert result is not None
        assert "get_supporters:0:None" in cli.calls
        await drain_background(svc)


class TestLoadSupporters:
    async def test_single_page(self, svc, db_session):
        cli = FakeMAClient()
        cli.supporter_pages[0] = SimplePage([make_supporter()], total=1)
        items = await svc.load_supporters(cli)
        assert len(items) == 1
        assert cli.calls == ["get_supporters:0:None"]
        await drain_background(svc)

    async def test_multipage_gathered_and_saved(self, svc, db_session):
        cli = FakeMAClient()
        page1 = SimplePage([make_supporter(sid=SID, name="u0")], total=250)
        page2 = SimplePage([make_supporter(sid=SID2, name="u1")], total=250)
        page3 = SimplePage([make_supporter(sid=SID3, name="u2")], total=250)
        cli.supporter_pages = {0: page1, 100: page2, 200: page3}
        items = await svc.load_supporters(cli)
        assert len(items) == 3
        assert set(cli.calls) == {"get_supporters:0:None", "get_supporters:100:None", "get_supporters:200:None"}
        # Фоновое сохранение в БД
        await drain_background(svc)
        from sqlalchemy import select

        rows = (await db_session.execute(select(MemealertsSupporters))).scalars().all()
        assert {r.id for r in rows} == {SID, SID2, SID3}


class TestSaveAllSupportersIntoDb:
    async def test_empty_list_no_db_call(self):
        def exploding_factory() -> Any:
            raise AssertionError("db must not be touched")

        await MemealertsService(db_session_factory=exploding_factory).save_all_supporters_into_db([])

    async def test_dedup_by_supporter_id(self, svc, db_session):
        sup1 = make_supporter(sid=SID, name="first")
        sup2 = make_supporter(sid=SID, name="duplicate-id")
        await svc.save_all_supporters_into_db([sup1, sup2])
        rows = (await db_session.execute(select(MemealertsSupporters))).scalars().all()
        assert len(rows) == 1
        # Последний выигрывает (dict comprehension)
        assert rows[0].name == "duplicate-id"

    async def test_upsert_updates_existing(self, svc, db_session):
        db_session.add(MemealertsSupporters(id=SID, name="old", link="old_link"))
        await db_session.commit()
        await svc.save_all_supporters_into_db([make_supporter(sid=SID, name="new")])
        rows = (await db_session.execute(select(MemealertsSupporters))).scalars().all()
        assert len(rows) == 1
        assert rows[0].name == "new"

    async def test_link_fallback_to_name(self, svc, db_session):
        await svc.save_all_supporters_into_db([make_supporter(sid=SID, name="named", link=None)])
        rows = (await db_session.execute(select(MemealertsSupporters))).scalars().all()
        assert rows[0].link == "named"


class TestSearchSupporterFromDb:
    async def test_by_name(self, svc, db_session):
        db_session.add(MemealertsSupporters(id=SID, name="nick", link="l1"))
        await db_session.commit()
        row = await svc.search_supporter_from_db("nick")
        assert row is not None
        assert row.id == SID

    async def test_by_link(self, svc, db_session):
        db_session.add(MemealertsSupporters(id=SID2, name="n2", link="tg_link"))
        await db_session.commit()
        row = await svc.search_supporter_from_db("tg_link")
        assert row is not None
        assert row.id == SID2

    async def test_not_found(self, svc):
        assert await svc.search_supporter_from_db("ghost") is None

    async def test_multiple_rows_raise(self, svc, db_session):
        db_session.add(MemealertsSupporters(id=SID, name="dup", link="l1"))
        db_session.add(MemealertsSupporters(id=SID2, name="dup", link="l2"))
        await db_session.commit()
        with pytest.raises(MultipleResultsFound):
            await svc.search_supporter_from_db("dup")
