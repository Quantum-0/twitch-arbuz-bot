# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты обработчиков EventSub (services/eventsub_service.py).

Ревокации покрыты test_eventsub_revocation.py, v1/v2-фолбэк мемкоинов —
test_eventsub_memealerts_fallback.py, авто-фикс наград — test_eventsub_reward_autofulfill.py.
Здесь: SSE-события оверлея, диспетчер наград, баны, AI-стикеры, TTS, стикеры в TG.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.eventsub_service import TwitchEventSubService
from services.stickers import ModerationBlockedException, RewardRedemptionProcessingError
from utils.enums import SSEChannel

BROADCASTER_ID = "123456"


async def settle(coro_mock, ticks: int = 25):
    """Ждём, пока fire-and-forget задача (task_wrapper) доведёт мок до вызова."""
    for _ in range(ticks):
        if coro_mock.await_count > 0:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"Мок не был вызван: {coro_mock}")


def build_service(**deps) -> TwitchEventSubService:
    service = object.__new__(TwitchEventSubService)
    service._statistics = deps.get("statistics")
    service._ssem = SimpleNamespace(has_clients=AsyncMock(return_value=False), broadcast=AsyncMock())
    service._chatbot = SimpleNamespace(send_message=AsyncMock())
    service._twitch = SimpleNamespace(
        shoutout=AsyncMock(),
        unsubscribe_raid=AsyncMock(),
        cancel_redemption=AsyncMock(),
        fulfill_redemption=AsyncMock(),
        send_warning=AsyncMock(),
    )
    service._stickers = SimpleNamespace(build_sticker=AsyncMock(return_value="st-1"))
    service._moderation = SimpleNamespace(validate=lambda text: SimpleNamespace(is_banned=False, found_word=None))
    service._mqtt = SimpleNamespace(publish=AsyncMock())
    service._bans = deps.get("bans")
    service._cache = None
    service._db_session_factory = None
    for name, dep in deps.items():
        if name != "statistics" and name != "bans":
            setattr(service, f"_{name}", dep)
    return service


def build_user(**overrides) -> SimpleNamespace:
    user = SimpleNamespace(
        id=1,
        twitch_id=BROADCASTER_ID,
        login_name="streamer",
        settings=SimpleNamespace(enable_shoutout_on_raid=False, ai_sticker_reward_id=None),
        memealerts=SimpleNamespace(memealerts_reward=None, access_token=None, memealerts_token=None),
        tts=SimpleNamespace(enabled=True, tts_reward_id=None, max_length=200, model="tts-1", read_username=False),
        telegram=SimpleNamespace(
            telegram_user_id=None, stickers_enabled=False, stickers_chat_id=None, stickers_mode="photo"
        ),
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def follow_payload(user_name: str = "follower"):
    return SimpleNamespace(
        event=SimpleNamespace(broadcaster_user_id=BROADCASTER_ID, user_name=user_name),
    )


def subscribe_payload():
    return SimpleNamespace(
        event=SimpleNamespace(broadcaster_user_id=BROADCASTER_ID, user_name="subber", tier="1000", is_gift=False),
    )


def resub_payload():
    return SimpleNamespace(
        event=SimpleNamespace(broadcaster_user_id=BROADCASTER_ID, user_name="subber", cumulative_months=7),
    )


def make_tts(enabled=True, reward="rew-t", read_username=False, model="tts-1"):
    return SimpleNamespace(
        enabled=enabled, tts_reward_id=reward, max_length=200, model=model, read_username=read_username
    )


def raid_payload(from_id: str = "999", viewers: int = 10):
    return SimpleNamespace(
        event=SimpleNamespace(
            to_broadcaster_user_id=BROADCASTER_ID,
            from_broadcaster_user_id=from_id,
            from_broadcaster_user_name="raider",
            viewers=viewers,
        ),
        subscription=SimpleNamespace(subscription_id="sub-raid"),
    )


def redemption_payload(reward_id: str, user_input: str = "text", user_login: str = "viewer", user_id: str = "42"):
    return SimpleNamespace(
        event=SimpleNamespace(
            broadcaster_user_id=BROADCASTER_ID,
            user_input=user_input,
            user_login=user_login,
            user_name=user_login.capitalize(),
            user_id=user_id,
            redemption_id="red-1",
        ),
        subscription=SimpleNamespace(condition=SimpleNamespace(reward_id=reward_id)),
    )


# ── SSE-события оверлея (follow / sub / resub / raid) ───────────────────────


async def test_follow_broadcasts_to_overlay():
    service = build_service()
    service._ssem.has_clients.return_value = True
    service._get_user_by_id_or_login = AsyncMock()

    await service.handle_follow(follow_payload("nick"))
    await settle(service._ssem.broadcast)

    args = service._ssem.broadcast.await_args.args
    assert args[0] == BROADCASTER_ID
    assert args[1] == SSEChannel.TWITCH_EVENTS
    assert json.loads(args[2]) == {"type": "follow", "user": "nick"}


async def test_follow_without_clients_is_quiet():
    service = build_service()
    service._get_user_by_id_or_login = AsyncMock()

    await service.handle_follow(follow_payload())
    await asyncio.sleep(0)

    service._ssem.broadcast.assert_not_awaited()


async def test_subscribe_broadcasts_tier_and_gift():
    service = build_service()
    service._ssem.has_clients.return_value = True

    await service.handle_subscribe(subscribe_payload())
    await settle(service._ssem.broadcast)

    event = json.loads(service._ssem.broadcast.await_args.args[2])
    assert event == {"type": "sub", "user": "subber", "tier": "1000", "gift": False}


async def test_resub_broadcasts_months():
    service = build_service()
    service._ssem.has_clients.return_value = True

    await service.handle_subscription_message(resub_payload())
    await settle(service._ssem.broadcast)

    event = json.loads(service._ssem.broadcast.await_args.args[2])
    assert event == {"type": "resub", "user": "subber", "months": 7}


async def test_raid_with_shoutout_enabled_shoutouts():
    service = build_service()
    user = build_user(settings=SimpleNamespace(enable_shoutout_on_raid=True))
    service._get_user_by_id_or_login = AsyncMock(return_value=user)

    await service.handle_raid(raid_payload())
    await settle(service._twitch.shoutout)

    service._twitch.shoutout.assert_awaited_once()
    assert service._twitch.shoutout.await_args.kwargs["shoutout_to"] == "999"
    service._twitch.unsubscribe_raid.assert_not_awaited()


async def test_raid_with_shoutout_disabled_unsubscribes():
    service = build_service()
    user = build_user(settings=SimpleNamespace(enable_shoutout_on_raid=False))
    service._get_user_by_id_or_login = AsyncMock(return_value=user)

    await service.handle_raid(raid_payload())
    await settle(service._twitch.unsubscribe_raid)

    service._twitch.unsubscribe_raid.assert_awaited_once_with(subscription_id="sub-raid")
    service._twitch.shoutout.assert_not_awaited()


async def test_raid_broadcasts_event_to_overlay():
    service = build_service()
    service._ssem.has_clients.return_value = True
    service._get_user_by_id_or_login = AsyncMock(return_value=build_user())

    await service.handle_raid(raid_payload(viewers=42))
    await settle(service._ssem.broadcast)

    event = json.loads(service._ssem.broadcast.await_args.args[2])
    assert event == {"type": "raid", "user": "raider", "count": 42}


# ── handle_reward_redemption: баны и диспетчеризация ────────────────────────


async def test_redemption_banned_chatter_cancelled():
    service = build_service(bans=SimpleNamespace(is_banned=AsyncMock(return_value=True)))
    user = build_user(memealerts=SimpleNamespace(memealerts_reward="rew-m", access_token=None, memealerts_token=None))
    payload = redemption_payload("rew-m")
    service._get_user_by_id_or_login = AsyncMock(return_value=user)
    reward_buy = AsyncMock()
    service.reward_buy_memealerts = reward_buy

    await service.handle_reward_redemption(payload)
    await settle(service._twitch.cancel_redemption)

    service._twitch.cancel_redemption.assert_awaited_once()
    reward_buy.assert_not_awaited()
    message = service._chatbot.send_message.await_args.args[1]
    assert "ограничен доступ" in message


async def test_redemption_dispatches_to_memealerts():
    service = build_service()
    user = build_user(memealerts=SimpleNamespace(memealerts_reward="rew-m", access_token=None, memealerts_token=None))
    service._get_user_by_id_or_login = AsyncMock(return_value=user)
    service._is_reward_banned = AsyncMock(return_value=False)
    reward_buy = AsyncMock()
    service.reward_buy_memealerts = reward_buy

    await service.handle_reward_redemption(redemption_payload("rew-m"))
    await settle(reward_buy)

    reward_buy.assert_awaited_once()


async def test_is_reward_banned_unknown_reward():
    service = build_service(bans=SimpleNamespace(is_banned=AsyncMock(return_value=True)))
    user = build_user()
    payload = SimpleNamespace(
        event=SimpleNamespace(user_id="42", user_login="viewer"),
        subscription=SimpleNamespace(condition=SimpleNamespace(reward_id="unknown-reward")),
    )

    result = await service._is_reward_banned(user, payload)

    assert result is False


async def test_is_reward_banned_checks_feature_match():
    bans = SimpleNamespace(is_banned=AsyncMock(return_value=False))
    service = build_service(bans=bans)
    user = build_user(tts=make_tts())

    result = await service._is_reward_banned(user, redemption_payload("rew-t"))

    assert result is False
    # фича бана определяется по типу награды: TTS → BanFeature.TTS
    assert bans.is_banned.await_args.args[:2] == ("42", BROADCASTER_ID)


async def test_dispatch_ai_sticker_moderation_blocked_increments_and_cancels():
    class FakeStatistics:
        def __init__(self):
            self.calls = []

        def inc(self, type_, subtype=""):
            self.calls.append((str(type_), subtype))

    statistics = FakeStatistics()
    service = build_service(statistics=statistics)
    user = build_user(settings=SimpleNamespace(enable_shoutout_on_raid=False, ai_sticker_reward_id="rew-a"))
    payload = redemption_payload("rew-a")

    async def raise_blocked(**kw):
        raise ModerationBlockedException("заблокировано модерацией")

    service.reward_ai_sticker = raise_blocked

    await service._dispatch_reward(user, payload)

    message = service._chatbot.send_message.await_args.args[1]
    assert message == "заблокировано модерацией"
    service._twitch.cancel_redemption.assert_awaited_once()
    assert ("reward_ai_stickers", "failed_on_moderation") in statistics.calls


async def test_dispatch_ai_sticker_error_without_cancel():
    service = build_service()
    user = build_user(settings=SimpleNamespace(enable_shoutout_on_raid=False, ai_sticker_reward_id="rew-a"))
    payload = redemption_payload("rew-a")

    async def raise_soft(**kw):
        raise RewardRedemptionProcessingError("мягкая ошибка", cancel_redemption=False)

    service.reward_ai_sticker = raise_soft

    await service._dispatch_reward(user, payload)

    service._chatbot.send_message.assert_awaited_once()
    service._twitch.cancel_redemption.assert_not_awaited()


async def test_dispatch_tts_error_sends_and_cancels():
    service = build_service()
    user = build_user(tts=make_tts())
    payload = redemption_payload("rew-t")

    async def raise_blocked(**kw):
        raise RewardRedemptionProcessingError("TTS недоступен")

    service.reward_tts = raise_blocked

    await service._dispatch_reward(user, payload)

    service._chatbot.send_message.assert_awaited_once()
    service._twitch.cancel_redemption.assert_awaited_once()


# ── reward_ai_sticker ───────────────────────────────────────────────────────


async def test_ai_sticker_empty_input_cancelled():
    service = build_service()
    user = build_user(settings=SimpleNamespace(enable_shoutout_on_raid=False, ai_sticker_reward_id="rew-a"))

    await service.reward_ai_sticker(user, redemption_payload("rew-a", user_input="   "))

    service._twitch.cancel_redemption.assert_awaited_once()
    service._stickers.build_sticker.assert_not_awaited()


async def test_ai_sticker_no_overlay_cancelled():
    service = build_service()
    user = build_user(settings=SimpleNamespace(enable_shoutout_on_raid=False, ai_sticker_reward_id="rew-a"))

    await service.reward_ai_sticker(user, redemption_payload("rew-a"))

    service._twitch.cancel_redemption.assert_awaited_once()
    assert "Оверлей" in service._chatbot.send_message.await_args.args[1]


async def test_ai_sticker_success_broadcasts():
    service = build_service()
    service._ssem.has_clients.return_value = True
    user = build_user(settings=SimpleNamespace(enable_shoutout_on_raid=False, ai_sticker_reward_id="rew-a"))
    service._stickers.build_sticker = AsyncMock(return_value="0f0e0d0c-1b2a")

    await service.reward_ai_sticker(user, redemption_payload("rew-a", user_input="котик"))

    service._ssem.broadcast.assert_awaited_once()
    args = service._ssem.broadcast.await_args.args
    assert args[1] == SSEChannel.AI_STICKER
    assert json.loads(args[2]) == {"sticker_file_id": "0f0e0d0c-1b2a"}


async def test_ai_sticker_sent_to_telegram():
    service = build_service()
    service._ssem.has_clients.return_value = True
    user = build_user(
        settings=SimpleNamespace(enable_shoutout_on_raid=False, ai_sticker_reward_id="rew-a"),
        telegram=SimpleNamespace(
            telegram_user_id=None, stickers_enabled=True, stickers_chat_id="-100500", stickers_mode="photo"
        ),
    )

    await service.reward_ai_sticker(user, redemption_payload("rew-a", user_input="котик"))

    service._mqtt.publish.assert_awaited_once()
    topic, payload = service._mqtt.publish.await_args.args
    assert topic == "telegram/send_photo"
    assert payload["chat_id"] == "-100500"
    assert payload["photo_url"].startswith("http")
    assert "котик" in payload["caption"]


async def test_sticker_to_telegram_document_mode():
    service = build_service()
    user = build_user(
        telegram=SimpleNamespace(
            telegram_user_id=None, stickers_enabled=True, stickers_chat_id="-100500", stickers_mode="document"
        )
    )

    await service._maybe_send_sticker_to_telegram(user, "st-1", "промпт", "viewer")

    topic = service._mqtt.publish.await_args.args[0]
    assert topic == "telegram/send_document"
    assert "document_url" in service._mqtt.publish.await_args.args[1]


async def test_sticker_to_telegram_disabled_is_quiet():
    service = build_service()
    user = build_user(
        telegram=SimpleNamespace(
            telegram_user_id=None, stickers_enabled=False, stickers_chat_id=None, stickers_mode="photo"
        )
    )

    await service._maybe_send_sticker_to_telegram(user, "st-1", "p", "v")

    service._mqtt.publish.assert_not_awaited()


# ── reward_tts ──────────────────────────────────────────────────────────────


async def test_tts_empty_text_is_penalty_fulfill():
    service = build_service()
    user = build_user(tts=make_tts())

    await service.reward_tts(user, redemption_payload("rew-t", user_input="  "))

    service._twitch.fulfill_redemption.assert_awaited_once()
    assert "штраф" in service._chatbot.send_message.await_args.args[1]


async def test_tts_disabled_cancelled():
    service = build_service()
    user = build_user(tts=make_tts(enabled=False))

    await service.reward_tts(user, redemption_payload("rew-t"))

    service._twitch.cancel_redemption.assert_awaited_once()
    service._ssem.broadcast.assert_not_awaited()


async def test_tts_no_overlay_cancelled():
    service = build_service()
    user = build_user(tts=make_tts())

    await service.reward_tts(user, redemption_payload("rew-t"))

    service._twitch.cancel_redemption.assert_awaited_once()


async def test_tts_moderation_blocked_warns_and_keeps_points():
    service = build_service()
    service._moderation = SimpleNamespace(
        validate=lambda text: SimpleNamespace(is_banned=True, found_word="запрещёнка")
    )
    service._ssem.has_clients.return_value = True
    user = build_user(tts=make_tts())

    await service.reward_tts(user, redemption_payload("rew-t", user_input="запрещёнка"))

    service._ssem.broadcast.assert_not_awaited()
    service._twitch.send_warning.assert_awaited_once()
    service._twitch.fulfill_redemption.assert_awaited_once()  # баллы не возвращаем


async def test_tts_success_broadcasts_text():
    service = build_service()
    service._ssem.has_clients.return_value = True
    user = build_user(tts=make_tts(model="voice-x"))

    await service.reward_tts(user, redemption_payload("rew-t", user_input="привет мир"))

    service._ssem.broadcast.assert_awaited_once()
    args = service._ssem.broadcast.await_args.args
    assert args[1] == SSEChannel.TTS
    payload = json.loads(args[2])
    assert payload == {"text": "привет мир", "model": "voice-x"}
    service._twitch.fulfill_redemption.assert_awaited_once()


async def test_tts_read_username_prepends_speaker():
    service = build_service()
    service._ssem.has_clients.return_value = True
    user = build_user(tts=make_tts(read_username=True))

    await service.reward_tts(user, redemption_payload("rew-t", user_input="привет"))

    payload = json.loads(service._ssem.broadcast.await_args.args[2])
    assert payload["text"].startswith("Viewer говорит")


async def test_tts_send_warning_failure_does_not_break():
    service = build_service()
    service._moderation = SimpleNamespace(
        validate=lambda text: SimpleNamespace(is_banned=True, found_word="запрещёнка")
    )
    service._ssem.has_clients.return_value = True
    service._twitch.send_warning = AsyncMock(side_effect=RuntimeError("api down"))
    user = build_user(tts=make_tts())

    await service.reward_tts(user, redemption_payload("rew-t", user_input="запрещёнка"))

    service._twitch.fulfill_redemption.assert_awaited_once()


# ── _get_user_by_id_or_login (настоящей БД из фикстур) ──────────────────────


class SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


async def test_get_user_by_twitch_id(db_session, test_user):
    service = build_service()
    service._db_session_factory = lambda: SessionCtx(db_session)

    user = await service._get_user_by_id_or_login(int(test_user.twitch_id))

    assert user.id == test_user.id


async def test_get_user_by_login_case_insensitive(db_session, test_user):
    service = build_service()
    service._db_session_factory = lambda: SessionCtx(db_session)

    user = await service._get_user_by_id_or_login(test_user.login_name.upper())

    assert user.id == test_user.id


async def test_get_user_not_found_raises(db_session):
    service = build_service()
    service._db_session_factory = lambda: SessionCtx(db_session)

    with pytest.raises(Exception, match="User not found"):
        await service._get_user_by_id_or_login("ghost_user")


async def test_get_user_validation_errors():
    service = build_service()
    with pytest.raises(ValueError):
        await service._get_user_by_id_or_login("")
    with pytest.raises(ValueError):
        await service._get_user_by_id_or_login(None)
