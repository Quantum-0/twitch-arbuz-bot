# ruff: noqa: S101

"""Юнит-тесты подсистемы банов (services/bans.py + гейт ChatBot.on_message)."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from database.models import Ban
from schemas.enums import BanFeature, BanScope
from services.bans import MAX_BAN_DURATION, BanService, EffectiveBans, looks_like_duration, parse_ban_duration
from services.cache import Cache
from tests.unit.fixtures.fake_redis import FakeRedis, session_factory

# ----------------------------------------------------------------------
# EffectiveBans — чистая логика проверки
# ----------------------------------------------------------------------


def test_effective_bans_empty():
    bans = EffectiveBans.empty()
    assert not bans.is_banned(BanFeature.CHAT_BOT, "111")
    assert not bans.is_banned(BanFeature.SITE)
    assert not bans.is_site_banned()


def test_effective_bans_global_applies_to_any_channel():
    bans = EffectiveBans(global_features=frozenset({BanFeature.CHAT_BOT.value}))
    assert bans.is_banned(BanFeature.CHAT_BOT, "111")
    assert bans.is_banned(BanFeature.CHAT_BOT, "222")
    assert bans.is_banned(BanFeature.CHAT_BOT, None)
    assert not bans.is_banned(BanFeature.MEMECOINS, "111")
    assert not bans.is_site_banned()


def test_effective_bans_channel_local():
    bans = EffectiveBans(channel_features={"111": frozenset({BanFeature.MEMECOINS.value})})
    assert bans.is_banned(BanFeature.MEMECOINS, "111")
    assert not bans.is_banned(BanFeature.MEMECOINS, "222")
    assert not bans.is_banned(BanFeature.MEMECOINS, None)


def test_effective_bans_site():
    bans = EffectiveBans(global_features=frozenset({BanFeature.SITE.value}))
    assert bans.is_site_banned()
    assert bans.is_banned(BanFeature.SITE, None)


# ----------------------------------------------------------------------
# parse_ban_duration
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("24ч", timedelta(hours=24)),
        ("30м", timedelta(minutes=30)),
        ("3д", timedelta(days=3)),
        ("1нед", timedelta(weeks=1)),
        ("2h", timedelta(hours=2)),
        ("10s", timedelta(seconds=10)),
        ("5w", timedelta(weeks=5)),
        ("@24ч", None),  # @ — не срок
        ("abc", None),
        ("0ч", None),
        ("ч", None),
    ],
)
def test_parse_ban_duration(token, expected):
    assert parse_ban_duration(token) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("2ч24м", True),  # комбинированный срок — не парсится, но похож
        ("24ч", True),
        ("24 часа", True),
        ("abc", False),
        ("Vasya", False),
        ("123", False),
        ("@24ч", True),
    ],
)
def test_looks_like_duration(token, expected):
    assert looks_like_duration(token) is expected


# ----------------------------------------------------------------------
# BanService + БД
# ----------------------------------------------------------------------


@pytest.fixture
def ban_service(db_session) -> BanService:
    return BanService(db_session_factory=session_factory(db_session))


async def _issue(ban_service: BanService, **kwargs: Any) -> Ban:
    params: dict[str, Any] = {
        "target_twitch_id": "777",
        "target_login": "vasya",
        "target_name": "Vasya",
        "features": [BanFeature.CHAT_BOT],
        "scope": BanScope.CHANNEL,
        "channel_twitch_id": "123456",
        "channel_login": "test_user",
        "created_by_twitch_id": "1",
        "created_by_login": "someone",
        "created_by_role": "streamer",
    }
    params.update(kwargs)
    return await ban_service.issue(**params)


@pytest.mark.asyncio
async def test_channel_ban_blocks_only_that_channel(ban_service):
    await _issue(ban_service, features=[BanFeature.MEMECOINS, BanFeature.AI_STICKERS])

    assert await ban_service.is_banned("777", "123456", BanFeature.MEMECOINS)
    assert await ban_service.is_banned("777", "123456", BanFeature.AI_STICKERS)
    assert not await ban_service.is_banned("777", "123456", BanFeature.CHAT_BOT)
    assert not await ban_service.is_banned("777", "999", BanFeature.MEMECOINS)
    assert not await ban_service.is_site_banned("777")


@pytest.mark.asyncio
async def test_global_ban_blocks_all_channels(ban_service):
    await _issue(ban_service, scope=BanScope.GLOBAL, channel_twitch_id=None, channel_login=None)

    assert await ban_service.is_banned("777", "123456", BanFeature.CHAT_BOT)
    assert await ban_service.is_banned("777", "999", BanFeature.CHAT_BOT)
    assert await ban_service.is_banned("777", None, BanFeature.CHAT_BOT)


@pytest.mark.asyncio
async def test_issue_upsert_merges_features(ban_service, db_session):
    await _issue(ban_service, features=[BanFeature.CHAT_BOT])
    await _issue(ban_service, features=[BanFeature.MEMECOINS])

    db_session.expire_all()
    rows = await ban_service.list_bans()
    assert len(rows) == 1
    assert sorted(rows[0].features) == [BanFeature.CHAT_BOT.value, BanFeature.MEMECOINS.value]


@pytest.mark.asyncio
async def test_reban_does_not_shorten_permanent(ban_service, db_session):
    await _issue(ban_service)
    await _issue(ban_service, expires_at=datetime.utcnow() + timedelta(hours=24))

    db_session.expire_all()
    rows = await ban_service.list_bans(include_expired=True)
    assert len(rows) == 1
    assert rows[0].expires_at is None  # перманентный остался перманентным


@pytest.mark.asyncio
async def test_reban_takes_max_expiry(ban_service, db_session):
    later = datetime.utcnow() + timedelta(hours=2)
    await _issue(ban_service, expires_at=later)
    await _issue(ban_service, expires_at=datetime.utcnow() + timedelta(minutes=30))

    db_session.expire_all()
    rows = await ban_service.list_bans(include_expired=True)
    assert len(rows) == 1
    assert rows[0].expires_at == later  # MAX действующего и нового

    newest = datetime.utcnow() + timedelta(hours=5)
    await _issue(ban_service, expires_at=newest)
    db_session.expire_all()
    rows = await ban_service.list_bans(include_expired=True)
    assert rows[0].expires_at == newest


@pytest.mark.asyncio
async def test_channel_ban_rejects_site_feature(ban_service):
    with pytest.raises(ValueError, match="site"):
        await _issue(ban_service, features=[BanFeature.SITE])


@pytest.mark.asyncio
async def test_staff_cannot_receive_site_ban(ban_service):
    with pytest.raises(ValueError, match="staff"):
        await _issue(
            ban_service,
            target_login="quantum075",
            scope=BanScope.GLOBAL,
            channel_twitch_id=None,
            channel_login=None,
            features=[BanFeature.SITE],
        )
    # Без site — можно
    ban = await _issue(
        ban_service,
        target_login="quantum075",
        scope=BanScope.GLOBAL,
        channel_twitch_id=None,
        channel_login=None,
        features=[BanFeature.CHAT_BOT],
    )
    assert ban.features == [BanFeature.CHAT_BOT.value]


@pytest.mark.asyncio
async def test_expired_ban_is_inactive(ban_service):
    await _issue(ban_service, expires_at=datetime.utcnow() - timedelta(minutes=1))
    assert not await ban_service.is_banned("777", "123456", BanFeature.CHAT_BOT)
    assert await ban_service.list_bans(include_expired=True)
    assert not await ban_service.list_bans()


@pytest.mark.asyncio
async def test_temporary_ban_active_until_expiry(ban_service):
    await _issue(ban_service, expires_at=datetime.utcnow() + timedelta(hours=1))
    assert await ban_service.is_banned("777", "123456", BanFeature.CHAT_BOT)


@pytest.mark.asyncio
async def test_revoke_channel_bans(ban_service):
    await _issue(ban_service, features=[BanFeature.CHAT_BOT])
    await _issue(
        ban_service,
        features=[BanFeature.MEMECOINS],
        scope=BanScope.GLOBAL,
        channel_twitch_id=None,
        channel_login=None,
    )

    removed = await ban_service.revoke_channel_bans("777", "123456")
    assert removed == 1
    # Global-бан не тронут
    assert not await ban_service.is_banned("777", "123456", BanFeature.CHAT_BOT)
    assert await ban_service.is_banned("777", "999", BanFeature.MEMECOINS)


@pytest.mark.asyncio
async def test_is_registered_user(ban_service, test_user):
    assert await ban_service.is_registered_user(test_user.twitch_id)
    assert not await ban_service.is_registered_user("424242")


@pytest.mark.asyncio
async def test_issue_normalizes_aware_expires_at(ban_service):
    # Регрессия веб-панели: JS шлёт ISO с Z → pydantic даёт aware-datetime,
    # а asyncpg не пишет aware в naive-колонку (DataError → HTTP 500).
    from schemas.ban_schemas import BanCreateSchema

    payload = BanCreateSchema.model_validate(
        {"login": "vasya", "scope": "channel", "expires_at": "2027-01-01T12:00:00.000Z"}
    )
    assert payload.expires_at is not None
    assert payload.expires_at.tzinfo is not None  # aware на входе

    ban = await _issue(ban_service, expires_at=payload.expires_at)
    assert ban.expires_at == datetime(2027, 1, 1, 12, 0)

    # Не-UTC offset (+03:00) конвертируется в тот же момент времени
    msk = datetime.fromisoformat("2027-01-01T18:30:00+03:00")
    ban_msk = await _issue(ban_service, target_twitch_id="555", expires_at=msk)
    assert ban_msk.expires_at == datetime(2027, 1, 1, 15, 30)


@pytest.mark.asyncio
async def test_get_site_ban(ban_service):
    assert await ban_service.get_site_ban("123456") is None
    await _issue(
        ban_service,
        target_twitch_id="123456",
        target_login="test_user",
        scope=BanScope.GLOBAL,
        channel_twitch_id=None,
        channel_login=None,
        features=[BanFeature.SITE],
        reason="Нарушение правил",
    )
    ban = await ban_service.get_site_ban("123456")
    assert ban is not None
    assert ban.reason == "Нарушение правил"
    assert BanFeature.SITE.value in ban.features


# ----------------------------------------------------------------------
# Кэш (FakeRedis)
# ----------------------------------------------------------------------


@pytest.fixture
def fake_cache() -> tuple[Cache, FakeRedis]:
    fake = FakeRedis()
    cache = Cache(redis=fake, binary_redis=fake)
    return cache, fake


@pytest.mark.asyncio
async def test_cache_writes_and_invalidates(db_session, fake_cache):
    cache, fake = fake_cache
    service = BanService(db_session_factory=session_factory(db_session), cache=cache)

    await _issue(service, target_twitch_id="888")
    # issue() инвалидирует кэш таргета; после чтения — закэшировано
    await service.get_effective("888")
    assert "cache:bans:u:888" in fake.values

    # Повторная выдача инвалидирует: после удаления кэша чтение снова из БД
    del fake.values["cache:bans:u:888"]
    effective = await service.get_effective("888")
    assert effective.is_banned(BanFeature.CHAT_BOT, "123456")


@pytest.mark.asyncio
async def test_cache_negative_result(db_session, fake_cache):
    cache, fake = fake_cache
    service = BanService(db_session_factory=session_factory(db_session), cache=cache)

    effective = await service.get_effective("999")  # банов нет
    assert not effective.is_site_banned()
    assert "cache:bans:u:999" in fake.values  # отрицательный кэш записан


# ----------------------------------------------------------------------
# Гейт ChatBot.on_message
# ----------------------------------------------------------------------


def _chat_message(chatter_id: int = 777, chatter_login: str = "vasya", text: str = "!кусь") -> Any:
    from schemas.twitch import ChatMessageWebhookEventSchema

    return ChatMessageWebhookEventSchema.model_validate(
        {
            "broadcaster_user_id": 123456,
            "broadcaster_user_login": "test_user",
            "broadcaster_user_name": "test_user",
            "chatter_user_id": chatter_id,
            "chatter_user_login": chatter_login,
            "chatter_user_name": chatter_login.capitalize(),
            "message_id": "cc106a89-1814-919d-454c-f4f2f970aae7",
            "message": {
                "text": text,
                "fragments": [{"type": "text", "text": text, "cheermote": None, "emote": None, "mention": None}],
            },
            "color": "#00FF7F",
            "badges": [],
            "message_type": "text",
            "cheer": None,
            "reply": None,
            "channel_points_custom_reward_id": None,
        }
    )


@pytest.fixture
def chat_bot(db_session, state_manager, ban_service):
    from twitch.chat.bot import ChatBot

    bot = ChatBot(
        db_session_factory=session_factory(db_session),
        state_manager=state_manager,
        mqtt=SimpleNamespace(),
        statistics=None,
        sse_manager=None,
        bans=ban_service,
    )
    calls: list[str] = []

    async def _handle(user_settings, streamer, message):  # noqa: ARG001
        calls.append("commands")

    bot._command_manager = SimpleNamespace(handle=_handle)  # noqa: SLF001
    bot._handler_manager = SimpleNamespace(handle=_handle)  # noqa: SLF001
    bot._calls = calls  # noqa: SLF001
    return bot


@pytest.mark.asyncio
async def test_on_message_without_bans_runs_managers(chat_bot, test_user):  # noqa: ARG001
    await chat_bot.on_message(_chat_message())
    assert chat_bot._calls == ["commands", "commands"]  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_banned_chatter_skips_managers(chat_bot, ban_service):
    await _issue(ban_service)
    await chat_bot.on_message(_chat_message())
    assert chat_bot._calls == []  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_banned_on_other_channel_allows(chat_bot, ban_service, test_user):  # noqa: ARG001
    await _issue(ban_service, channel_twitch_id="999", channel_login="other")
    await chat_bot.on_message(_chat_message())
    assert chat_bot._calls == ["commands", "commands"]  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_streamer_chat_bot_ban_channel_served(chat_bot, ban_service, test_user):  # noqa: ARG001
    # Фичи бана действуют на таргет КАК НА ЗРИТЕЛЯ: global chat_bot-бан
    # стримера НЕ отключает бот на его канале — команды работают у других.
    await _issue(
        ban_service,
        target_twitch_id="123456",
        target_login="test_user",
        scope=BanScope.GLOBAL,
        channel_twitch_id=None,
        channel_login=None,
        features=[BanFeature.CHAT_BOT],
    )
    await chat_bot.on_message(_chat_message())  # пишет незабаненный зритель 777
    assert chat_bot._calls == ["commands", "commands"]  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_globally_banned_chatter_blocked_on_own_channel(chat_bot, ban_service, test_user):  # noqa: ARG001
    # Global chat_bot-бан блокирует команды ВЕЗДЕ, включая собственный канал.
    await _issue(
        ban_service,
        target_twitch_id="123456",
        target_login="test_user",
        scope=BanScope.GLOBAL,
        channel_twitch_id=None,
        channel_login=None,
        features=[BanFeature.CHAT_BOT],
    )
    await chat_bot.on_message(_chat_message(chatter_id=123456, chatter_login="test_user"))
    assert chat_bot._calls == []  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_site_banned_streamer_channel_served(chat_bot, ban_service, test_user):  # noqa: ARG001
    # Спецификация site-бана: закрывает только доступ к сайту/настройкам.
    # Бот на канале стримера продолжает работать, site-бан канал НЕ глушит.
    await _issue(
        ban_service,
        target_twitch_id="123456",
        target_login="test_user",
        scope=BanScope.GLOBAL,
        channel_twitch_id=None,
        channel_login=None,
        features=[BanFeature.SITE],
    )
    await chat_bot.on_message(_chat_message())
    assert chat_bot._calls == ["commands", "commands"]  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_chatter_site_ban_allows_commands(chat_bot, ban_service, test_user):  # noqa: ARG001
    # Зритель с global-баном только по site: на чужих каналах чат-бот
    # отвечает ему как обычно (site ≠ chat_bot).
    await _issue(
        ban_service,
        scope=BanScope.GLOBAL,
        channel_twitch_id=None,
        channel_login=None,
        features=[BanFeature.SITE],
    )
    await chat_bot.on_message(_chat_message())
    assert chat_bot._calls == ["commands", "commands"]  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_chatter_feature_other_than_chat_bot_allows(chat_bot, ban_service, test_user):  # noqa: ARG001
    await _issue(ban_service, features=[BanFeature.AI_STICKERS])
    await chat_bot.on_message(_chat_message())
    assert chat_bot._calls == ["commands", "commands"]  # noqa: SLF001


# ----------------------------------------------------------------------
# Устойчивость: overflow срока, битый кэш, rate limit
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    ["999999999999д", "999999999999999нед", "999999999999999999с", "9999999999999999999999h"],
)
def test_parse_ban_duration_overflow_returns_none(token):
    # Гигантские числа раньше роняли команду OverflowError'ом — теперь None.
    assert parse_ban_duration(token) is None


def test_max_ban_duration_cap():
    assert parse_ban_duration("366д") == MAX_BAN_DURATION
    assert parse_ban_duration("500д") == timedelta(days=500)
    assert parse_ban_duration("500д") > MAX_BAN_DURATION
    assert looks_like_duration("500д")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ['{"c": [1, 2]}', '"just a string"', "[1, 2, 3]", "{not json"])
async def test_get_effective_survives_corrupt_cache(db_session, fake_cache, payload):
    cache, fake = fake_cache
    service = BanService(db_session_factory=session_factory(db_session), cache=cache)
    fake.values["cache:bans:u:777"] = payload

    effective = await service.get_effective("777")  # не падает — читает из БД

    assert not effective.is_banned(BanFeature.CHAT_BOT, "123456")


@pytest.mark.asyncio
async def test_ban_create_rate_limit_semantics(fake_cache):
    cache, _ = fake_cache
    for _ in range(10):
        assert await cache.check_rate_limit("bans_create:1", 10, 60)
    assert not await cache.check_rate_limit("bans_create:1", 10, 60)
    # Другой пользователь не зависит от чужого лимита
    assert await cache.check_rate_limit("bans_create:2", 10, 60)


@pytest.mark.asyncio
async def test_check_rate_limit_heals_missing_ttl(fake_cache):
    # Ключ остался без TTL (крэш между INCR и EXPIRE) — без лечения вечный 429.
    cache, fake = fake_cache
    fake.values["rate_limit:heal"] = "5"
    assert fake.ttls.get("rate_limit:heal") is None

    assert await cache.check_rate_limit("heal", 10, 60)  # 6 ≤ 10 — пропускаем

    assert fake.ttls.get("rate_limit:heal") == 60  # TTL восстановлен


# ----------------------------------------------------------------------
# TTSHandler: гейт бана по фиче tts (орзвучка через чат)
# ----------------------------------------------------------------------


class _FakeBans:
    def __init__(self, banned: bool) -> None:
        self._banned = banned
        self.calls: list[tuple[str, str, BanFeature]] = []

    async def is_banned(self, target_twitch_id: str, channel_twitch_id: str, feature: BanFeature) -> bool:
        self.calls.append((target_twitch_id, channel_twitch_id, feature))
        return self._banned


@pytest.fixture
def tts_harness():
    """TTSHandler с фейковым контейнером: (banned) → (handler, bans, sse, stats)."""
    import container_runtime
    from twitch.chat.handlers.tts import TTSHandler

    class _FakeSSE:
        def __init__(self):
            self.broadcasts: list[Any] = []

        async def has_clients(self, user_id: int, channel: Any) -> bool:  # noqa: ARG002
            return True

        async def broadcast(self, user_id: int, channel: Any, payload: str) -> None:
            self.broadcasts.append((user_id, channel, payload))

    class _FakeStats:
        def __init__(self):
            self.incs: list[Any] = []

        def inc(self, type_: Any, subtype: Any = None) -> None:
            self.incs.append((type_, subtype))

    def _make(banned: bool):
        bans = _FakeBans(banned)
        sse, stats = _FakeSSE(), _FakeStats()
        fake_container = SimpleNamespace(
            bans=lambda: bans,
            sse_manager=lambda: sse,
            statistics=lambda: stats,
            moderation_service=lambda: SimpleNamespace(validate=lambda _text: SimpleNamespace(is_banned=False)),
        )
        old = container_runtime._container  # noqa: SLF001
        container_runtime._container = fake_container

        async def _send(*_args: Any, **_kwargs: Any) -> None:
            return None

        try:
            handler = TTSHandler(None, _send, None)
        finally:
            container_runtime._container = old
        return handler, bans, sse, stats

    return _make


def _tts_streamer() -> Any:
    from database.models import TTSSettings

    return SimpleNamespace(
        id=1,
        twitch_id="123456",
        login_name="test_user",
        tts=TTSSettings(
            user_id=1,
            enabled=True,
            tts_reward_id=None,
            read_username=False,
            permissions={"roles": {"chatter": {"all_no_replies": True}}},
            cooldown_per_user=0,
            cooldown_per_channel=0,
            max_length=100,
            model="test",
            external_key=None,
        ),
    )


@pytest.mark.asyncio
async def test_tts_handler_skips_banned_chatter(tts_harness):
    from schemas.api import StatsType
    from twitch.chat.handlers.handlers import HandlerResult

    handler, bans, sse, stats = tts_harness(banned=True)
    result = await handler.handle(_tts_streamer(), _chat_message())
    assert result == HandlerResult.SKIPED
    assert sse.broadcasts == []
    assert (StatsType.TTS_BLOCKED, "banned") in stats.incs
    assert bans.calls == [("777", "123456", BanFeature.TTS)]


@pytest.mark.asyncio
async def test_tts_handler_voices_unbanned_chatter(tts_harness):
    from schemas.api import StatsType
    from twitch.chat.handlers.handlers import HandlerResult

    handler, bans, sse, stats = tts_harness(banned=False)
    result = await handler.handle(_tts_streamer(), _chat_message())
    assert result == HandlerResult.HANDLED_AND_CONTINUE
    assert len(sse.broadcasts) == 1
    assert (StatsType.TTS_BLOCKED, "banned") not in stats.incs
