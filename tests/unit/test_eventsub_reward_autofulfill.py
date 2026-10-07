"""Юнит-тесты notify_reward_autofulfill: авто-фикс «автоматически выполнять» + TG-уведомление."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from twitchAPI.type import TwitchResourceNotFound

from services.eventsub_service import TwitchEventSubService

REWARD_ID = "aaaabbbb-cccc-dddd-eeee-ffff00001111"


def make_cache(dedup_slot_taken: bool = False) -> SimpleNamespace:
    return SimpleNamespace(set_str_nx=AsyncMock(return_value=not dedup_slot_taken))


def build_service(telegram_user_id: str | None, fixed: bool = True, cache=None):
    service = object.__new__(TwitchEventSubService)
    service._cache = cache
    service._db_session_factory = None
    service._mqtt = SimpleNamespace(publish=AsyncMock())
    service._twitch = SimpleNamespace(fix_reward_autofulfill=AsyncMock(return_value=fixed))
    user = SimpleNamespace(
        id=1,
        twitch_id="654179372",
        telegram=SimpleNamespace(telegram_user_id=telegram_user_id),
    )
    return service, user


@pytest.mark.asyncio
async def test_autofulfill_fixes_and_notifies() -> None:
    cache = make_cache()
    service, user = build_service("42", fixed=True, cache=cache)

    await TwitchEventSubService.notify_reward_autofulfill(service, user, REWARD_ID)

    service._twitch.fix_reward_autofulfill.assert_awaited_once_with(user, REWARD_ID)
    service._mqtt.publish.assert_awaited_once()
    payload = service._mqtt.publish.await_args.args[1]
    assert payload["request_id"] == "reward_autofulfill:654179372"
    assert payload["chat_id"] == "42"
    assert "автоматически выполнять" in payload["message_text"]
    cache.set_str_nx.assert_awaited_once_with(
        f"reward_autofulfill_notified:654179372:{REWARD_ID}", "1", ttl=86400
    )


@pytest.mark.asyncio
async def test_autofulfill_flag_already_off_is_quiet() -> None:
    """404 от гонки дублей вебхука (флаг уже выключен) — чинить нечего, тишина."""
    cache = make_cache()
    service, user = build_service("42", fixed=False, cache=cache)

    await TwitchEventSubService.notify_reward_autofulfill(service, user, REWARD_ID)

    service._mqtt.publish.assert_not_awaited()
    cache.set_str_nx.assert_not_awaited()


@pytest.mark.asyncio
async def test_autofulfill_dedup_active() -> None:
    cache = make_cache(dedup_slot_taken=True)
    service, user = build_service("42", fixed=True, cache=cache)

    await TwitchEventSubService.notify_reward_autofulfill(service, user, REWARD_ID)

    service._twitch.fix_reward_autofulfill.assert_awaited_once()
    service._mqtt.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_autofulfill_without_binding_fixes_but_no_message() -> None:
    """Без личной привязки TG галочка всё равно выключается, сообщение не шлётся."""
    cache = make_cache()
    service, user = build_service(None, fixed=True, cache=cache)

    await TwitchEventSubService.notify_reward_autofulfill(service, user, REWARD_ID)

    service._twitch.fix_reward_autofulfill.assert_awaited_once()
    service._mqtt.publish.assert_not_awaited()
    cache.set_str_nx.assert_not_awaited()


@pytest.mark.asyncio
async def test_autofulfill_swallows_twitch_errors() -> None:
    cache = make_cache()
    service, user = build_service("42", cache=cache)
    service._twitch.fix_reward_autofulfill = AsyncMock(side_effect=RuntimeError("twitch down"))

    await TwitchEventSubService.notify_reward_autofulfill(service, user, REWARD_ID)  # не должен бросить

    service._mqtt.publish.assert_not_awaited()


def make_404_service(side_effect: Exception) -> tuple[TwitchEventSubService, SimpleNamespace]:
    service = object.__new__(TwitchEventSubService)
    service._cache = None
    service._db_session_factory = None
    service._mqtt = SimpleNamespace(publish=AsyncMock())
    service._twitch = SimpleNamespace(
        cancel_redemption=AsyncMock(side_effect=side_effect),
        fulfill_redemption=AsyncMock(side_effect=side_effect),
    )
    service.notify_reward_autofulfill = AsyncMock()
    user = SimpleNamespace(id=1, twitch_id="654179372", telegram=None)
    return service, user


def make_redemption_payload() -> SimpleNamespace:
    return SimpleNamespace(
        subscription=SimpleNamespace(condition=SimpleNamespace(reward_id=REWARD_ID)),
        event=SimpleNamespace(redemption_id="redemption-1"),
    )


@pytest.mark.asyncio
async def test_cancel_redemption_404_triggers_autofulfill_notify() -> None:
    service, user = make_404_service(TwitchResourceNotFound("already resolved"))

    await service._cancel_redemption(user, make_redemption_payload())
    await asyncio.sleep(0)  # fire-and-forget задача

    service.notify_reward_autofulfill.assert_awaited_once_with(user, REWARD_ID)


@pytest.mark.asyncio
async def test_fulfill_redemption_404_triggers_autofulfill_notify() -> None:
    service, user = make_404_service(TwitchResourceNotFound("already resolved"))

    await service._fulfill_redemption(user, make_redemption_payload())
    await asyncio.sleep(0)

    service.notify_reward_autofulfill.assert_awaited_once_with(user, REWARD_ID)
