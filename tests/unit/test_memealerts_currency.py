# ruff: noqa: S101

"""Пользовательские склонения названия валюты: property, API, синхронизация."""

from types import SimpleNamespace
from typing import Any

from database.models import MemealertsSettings
from routers.web.memealerts_routes import _store_channel_info


def test_currency_property_override_wins_over_ma() -> None:
    settings = MemealertsSettings(
        ma_memecoin_name_genitive="мемкоина",
        memecoin_name_genitive_override="печеньки",
    )
    assert settings.memecoin_name_genitive == "печеньки"


def test_currency_property_falls_back_to_ma() -> None:
    settings = MemealertsSettings(
        ma_memecoin_name_accusative="мемкоин",
        ma_memecoin_name_genitive_multiple="мемкоинов",
        ma_memecoin_name_accusative_multiple="мемкоины",
    )
    assert settings.memecoin_name_accusative == "мемкоин"
    assert settings.memecoin_name_genitive_multiple == "мемкоинов"
    assert settings.memecoin_name_accusative_multiple == "мемкоины"


def test_currency_property_none_when_no_values() -> None:
    settings = MemealertsSettings()
    assert settings.memecoin_name_genitive is None
    assert settings.memecoin_name_accusative_multiple is None


class _SessionCtx:
    """Обёртка сессии фикстуры под сигнатуру db_session_factory (без close)."""

    def __init__(self, session: Any):
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


async def test_currency_patch_sets_strips_and_resets(client, user_auth_mock, test_user, db_session):  # noqa: ARG001
    test_user.memealerts.ma_memecoin_name_genitive = "мемкоина"
    await db_session.commit()

    resp = await client.patch("/api/user/memealerts/currency", json={"genitive": "  печеньки  "})
    assert resp.status_code == 200
    await db_session.refresh(test_user.memealerts)
    assert test_user.memealerts.memecoin_name_genitive_override == "печеньки"  # strip
    assert test_user.memealerts.memecoin_name_genitive == "печеньки"  # property: override
    assert test_user.memealerts.ma_memecoin_name_genitive == "мемкоина"  # авто не тронуто

    resp = await client.patch("/api/user/memealerts/currency", json={"genitive": ""})
    assert resp.status_code == 200
    await db_session.refresh(test_user.memealerts)
    assert test_user.memealerts.memecoin_name_genitive_override is None  # сброс
    assert test_user.memealerts.memecoin_name_genitive == "мемкоина"  # снова авто


async def test_currency_requires_auth(client):  # noqa: ARG001
    resp = await client.patch("/api/user/memealerts/currency", json={"genitive": "печеньки"})
    assert resp.status_code in (401, 403)


async def test_currency_too_long_rejected(client, user_auth_mock, test_user):  # noqa: ARG001
    resp = await client.patch("/api/user/memealerts/currency", json={"genitive": "а" * 33})
    assert resp.status_code == 422


async def test_store_channel_info_updates_ma_and_keeps_override(db_session, test_user):
    # Синхронизация с Memealerts пишет только ma_*-колонки: пользовательский
    # override переживает повторную синхронизацию.
    test_user.memealerts.memecoin_name_genitive_override = "печеньки"
    await db_session.commit()

    channel = SimpleNamespace(
        currency_name_declensions=SimpleNamespace(
            genitive="мемкоина",
            accusative="мемкоин",
            multiple=SimpleNamespace(genitive="мемкоинов", accusative="мемкоины"),
        ),
        unique_link=None,
    )
    await _store_channel_info(lambda: _SessionCtx(db_session), test_user.id, channel)

    await db_session.refresh(test_user.memealerts)
    assert test_user.memealerts.ma_memecoin_name_genitive == "мемкоина"
    assert test_user.memealerts.ma_memecoin_name_accusative == "мемкоин"
    assert test_user.memealerts.ma_memecoin_name_genitive_multiple == "мемкоинов"
    assert test_user.memealerts.ma_memecoin_name_accusative_multiple == "мемкоины"
    assert test_user.memealerts.memecoin_name_genitive_override == "печеньки"  # не затёрт
    assert test_user.memealerts.memecoin_name_genitive == "печеньки"
