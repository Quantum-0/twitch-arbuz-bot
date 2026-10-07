import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from schemas.twitch import EventSubRevocationSchema
from services.eventsub_service import TwitchEventSubService

# Реальный payload из прода (GlitchTip): Twitch ретраил его из-за 422,
# потому что схемы ждали поле event.
REVOCATION_CHAT_PAYLOAD = {
    "subscription": {
        "id": "50016cc8-2f18-4fcf-9a6f-3db31eb3c4e7",
        "status": "authorization_revoked",
        "type": "channel.chat.message",
        "version": "1",
        "condition": {"broadcaster_user_id": "654179372", "user_id": "957818216"},
        "transport": {
            "method": "webhook",
            "callback": "https://bot.quantum0.ru/api/twitch/eventsub/654179372",
        },
        "created_at": "2026-08-18T15:06:10.916031757Z",
        "cost": 0,
    }
}


def build_service(cache=None) -> TwitchEventSubService:
    service = object.__new__(TwitchEventSubService)
    service._cache = cache
    service._db_session_factory = None
    service.notify_revocation = AsyncMock()
    return service


def build_revocation(sub_type: str, condition: dict) -> EventSubRevocationSchema:
    return EventSubRevocationSchema.model_validate(
        {
            "subscription": {
                "id": "50016cc8-2f18-4fcf-9a6f-3db31eb3c4e7",
                "status": "authorization_revoked",
                "type": sub_type,
                "version": "1",
                "condition": condition,
                "transport": {
                    "method": "webhook",
                    "callback": "https://bot.quantum0.ru/api/twitch/eventsub/1",
                },
                "created_at": "2026-08-18T15:06:10.916031757Z",
                "cost": 0,
            }
        }
    )


def test_revocation_schema_parses_real_payload() -> None:
    payload = EventSubRevocationSchema.model_validate(REVOCATION_CHAT_PAYLOAD)

    sub = payload.subscription
    assert sub.type == "channel.chat.message"
    assert sub.status == "authorization_revoked"
    assert sub.condition.broadcaster_user_id == 654179372
    assert sub.condition.user_id == 957818216


def test_revocation_schema_raid_condition() -> None:
    payload = build_revocation("channel.raid", {"to_broadcaster_user_id": "111", "from_broadcaster_user_id": "222"})

    assert payload.subscription.condition.to_broadcaster_user_id == 111
    assert payload.subscription.condition.broadcaster_user_id is None


@pytest.mark.asyncio
async def test_chat_message_revocation_disables_chat_bot() -> None:
    service = build_service()
    service._disable_chat_bot = AsyncMock()

    await service.handle_revocation(EventSubRevocationSchema.model_validate(REVOCATION_CHAT_PAYLOAD))
    await asyncio.sleep(0)  # даём выполниться fire-and-forget notify-задаче

    service._disable_chat_bot.assert_awaited_once_with(654179372)


@pytest.mark.asyncio
async def test_reward_revocation_touches_nothing() -> None:
    cache = SimpleNamespace(delete=AsyncMock())
    service = build_service(cache=cache)

    await service.handle_revocation(
        build_revocation(
            "channel.channel_points_custom_reward_redemption.add",
            {"broadcaster_user_id": "654179372", "reward_id": "aaaabbbb-cccc-dddd-eeee-ffff00001111"},
        )
    )
    await asyncio.sleep(0)

    cache.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_follow_revocation_clears_overlay_redis_key() -> None:
    cache = SimpleNamespace(delete=AsyncMock())
    service = build_service(cache=cache)

    await service.handle_revocation(build_revocation("channel.follow", {"broadcaster_user_id": "654179372"}))
    await asyncio.sleep(0)

    cache.delete.assert_awaited_once_with("eventsub:overlay:654179372")


@pytest.mark.asyncio
async def test_revocation_without_broadcaster_is_ignored() -> None:
    cache = SimpleNamespace(delete=AsyncMock())
    service = build_service(cache=cache)

    await service.handle_revocation(build_revocation("channel.follow", {}))

    cache.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_raid_revocation_with_empty_broadcaster_is_ignored() -> None:
    """Twitch присылает from/to_broadcaster_user_id="" для рейдов «из ниоткуда»."""
    cache = SimpleNamespace(delete=AsyncMock())
    service = build_service(cache=cache)

    payload = build_revocation("channel.raid", {"to_broadcaster_user_id": "", "from_broadcaster_user_id": ""})

    await service.handle_revocation(payload)

    cache.delete.assert_not_awaited()


# ── notify_revocation: TG-уведомление об отзыве авторизации ──────────────────


def build_notify_service(telegram_user_id: str | None, cache=None) -> TwitchEventSubService:
    service = object.__new__(TwitchEventSubService)
    service._cache = cache
    service._db_session_factory = None
    service._mqtt = SimpleNamespace(publish=AsyncMock())
    user = SimpleNamespace(id=1, telegram=SimpleNamespace(telegram_user_id=telegram_user_id))
    service._get_user_by_id_or_login = AsyncMock(return_value=user)
    return service


def make_cache(existing_key: bool = False) -> SimpleNamespace:
    """existing_key=True — дедуп-слот уже занят (set_str_nx возвращает False)."""
    return SimpleNamespace(get_str=AsyncMock(), set_str_nx=AsyncMock(return_value=not existing_key))


@pytest.mark.asyncio
async def test_notify_revocation_sends_message_and_sets_dedup() -> None:
    cache = make_cache()
    service = build_notify_service("42", cache=cache)

    await TwitchEventSubService.notify_revocation(service, 654179372, "channel.chat.message")

    service._mqtt.publish.assert_awaited_once()
    args = service._mqtt.publish.await_args.args
    assert args[0] == "telegram/send_message"
    assert args[1]["request_id"] == "revocation:654179372"
    assert args[1]["chat_id"] == "42"
    assert args[1]["message_text"] == (
        "Чат-бот отключён: Twitch отозвал авторизацию. Переавторизуйтесь на bot.quantum0.ru"
    )
    cache.set_str_nx.assert_awaited_once_with("revocation_notified:654179372", "1", ttl=86400)


@pytest.mark.asyncio
async def test_notify_revocation_default_text_for_unknown_kind() -> None:
    cache = make_cache()
    service = build_notify_service("42", cache=cache)

    await TwitchEventSubService.notify_revocation(service, 654179372, "channel.raid")

    text = service._mqtt.publish.await_args.args[1]["message_text"]
    assert text.startswith("Twitch-интеграция отключена")


@pytest.mark.asyncio
async def test_notify_revocation_dedup_active() -> None:
    cache = make_cache(existing_key=True)
    service = build_notify_service("42", cache=cache)

    await TwitchEventSubService.notify_revocation(service, 654179372, "channel.chat.message")

    service._mqtt.publish.assert_not_awaited()
    cache.set_str_nx.assert_awaited_once()  # слот проверен, но занят не нами → выход


@pytest.mark.asyncio
async def test_notify_revocation_without_binding_is_quiet(monkeypatch) -> None:
    """Нет личной привязки TG → тишина, но сброс стрим-тогла не выполнялся (не стрим-тип)."""
    reset = AsyncMock()
    monkeypatch.setattr("services.telegram_integration._disable_stream_notification", reset)
    cache = make_cache()
    service = build_notify_service(None, cache=cache)

    await TwitchEventSubService.notify_revocation(service, 654179372, "channel.chat.message")

    service._mqtt.publish.assert_not_awaited()
    cache.set_str_nx.assert_not_awaited()
    reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_revocation_stream_type_resets_toggle_without_binding(monkeypatch) -> None:
    """Стрим-тип: тогл снимается ДО guard'а привязки — работает и без telegram_user_id."""
    reset = AsyncMock()
    monkeypatch.setattr("services.telegram_integration._disable_stream_notification", reset)
    cache = make_cache()
    service = build_notify_service(None, cache=cache)

    await TwitchEventSubService.notify_revocation(service, 654179372, "stream.online")

    reset.assert_awaited_once_with(1, None)
    service._mqtt.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_revocation_stream_type_sends_and_resets(monkeypatch) -> None:
    reset = AsyncMock()
    monkeypatch.setattr("services.telegram_integration._disable_stream_notification", reset)
    cache = make_cache()
    service = build_notify_service("42", cache=cache)

    await TwitchEventSubService.notify_revocation(service, 654179372, "stream.offline")

    reset.assert_awaited_once_with(1, None)
    service._mqtt.publish.assert_awaited_once()
    cache.set_str_nx.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_revocation_user_not_found_is_swallowed() -> None:
    """Ошибки внутри notify_revocation глушатся (fire-and-forget безопасность)."""
    cache = make_cache()
    service = build_notify_service("42", cache=cache)
    service._get_user_by_id_or_login = AsyncMock(side_effect=Exception("user not found"))

    await TwitchEventSubService.notify_revocation(service, 654179372, "channel.chat.message")

    service._mqtt.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_revocation_fires_notify_task() -> None:
    service = build_service()
    service._disable_chat_bot = AsyncMock()

    await service.handle_revocation(EventSubRevocationSchema.model_validate(REVOCATION_CHAT_PAYLOAD))
    await asyncio.sleep(0)

    service.notify_revocation.assert_awaited_once_with(654179372, "channel.chat.message")
