# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Роут-тесты /api/user (routers/api/user_api.py): награды, настройки, TTS, референки.

Provide-зависимости подменяем через dependency_overrides: ключ достаём из
сигнатуры роута (каждый Provide[...] — уникальный объект), см. test_routes.py.
"""

import inspect
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock

import httpx
from jwt import DecodeError
from memealerts.types.exceptions import MATokenExpiredError
from twitchAPI.type import TwitchAPIException, TwitchResourceNotFound

from main import app
from routers.api import user_api
from routers.api.user_api import (
    get_not_shown_sticker_id,
    install_heat,
    setup_ai_stickers,
    setup_memealert,
    setup_tts,
    slovotron_tip,
    tts_external_speech,
    update_settings,
    upload_reference,
)
from services.twitch_token_service import TwitchTokenExpiredError
from utils.enums import SSEChannel


def _make_override(fake):
    # NB: `lambda f=fake: f` нельзя — callable-default Pydantic воспринимает как
    # factory и вызывает fake(), подменяя объект. Только замыкание без параметров.
    def override():
        return fake

    return override


@contextmanager
def provide_overrides(route, fakes: dict):
    keys = []
    for name, fake in fakes.items():
        dep = inspect.signature(route).parameters[name].annotation.__metadata__[0].dependency
        app.dependency_overrides[dep] = _make_override(fake)
        keys.append(dep)
    try:
        yield
    finally:
        for dep in keys:
            app.dependency_overrides.pop(dep, None)


class SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


def fake_twitch(**methods) -> Mock:
    twitch = Mock()
    twitch.install_heat_ext = AsyncMock()
    twitch.subscribe_raid = AsyncMock()
    twitch.unsubscribe_raid = AsyncMock()
    twitch.create_reward = AsyncMock(return_value=Mock(id=uuid.uuid4()))
    twitch.delete_reward = AsyncMock()
    twitch.subscribe_reward = AsyncMock()
    twitch.validate_reward_subscription = AsyncMock(return_value=None)
    twitch.update_reward = AsyncMock()
    twitch.send_warning = AsyncMock()
    for name, value in methods.items():
        setattr(twitch, name, value)
    return twitch


def http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.com")
    return httpx.HTTPStatusError("err", request=request, response=httpx.Response(status, request=request))


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


# ── not-shown-sticker-id / slovotron / install-heat ────────────────────────


async def test_not_shown_sticker_id_found(client, user_auth_mock):
    import uuid

    sticker_id = uuid.uuid4()
    stickers = Mock(get_unshown=AsyncMock(return_value=sticker_id))
    with provide_overrides(get_not_shown_sticker_id, {"stickers": stickers}):
        resp = await client.get("/api/user/not-shown-sticker-id", params={"channel": 1})

    assert resp.status_code == 200
    assert resp.json()["id"] == str(sticker_id)


async def test_not_shown_sticker_id_empty(client, user_auth_mock):
    stickers = Mock(get_unshown=AsyncMock(return_value=None))
    with provide_overrides(get_not_shown_sticker_id, {"stickers": stickers}):
        resp = await client.get("/api/user/not-shown-sticker-id", params={"channel": 1})

    assert resp.status_code == 404


async def test_slovotron_tip_sends_hint(client, user_auth_mock, test_user):
    chat_bot = Mock(send_message=AsyncMock())
    with provide_overrides(slovotron_tip, {"chat_bot": chat_bot}):
        resp = await client.post("/api/user/slovotron/tip", params={"channel": test_user.login_name})

    assert resp.status_code == 204
    chat_bot.send_message.assert_awaited_once()
    assert chat_bot.send_message.await_args.args[1] == "!подсказка"


async def test_slovotron_tip_unknown_channel(client, user_auth_mock):
    chat_bot = Mock(send_message=AsyncMock())
    with provide_overrides(slovotron_tip, {"chat_bot": chat_bot}):
        resp = await client.post("/api/user/slovotron/tip", params={"channel": "ghost"})

    assert resp.status_code == 404


async def test_install_heat_ok(client, user_auth_mock):
    twitch = fake_twitch()
    with provide_overrides(install_heat, {"twitch": twitch}):
        resp = await client.get("/api/user/install-heat")

    assert resp.status_code == 200
    assert resp.json() == {"result": True}


async def test_install_heat_reauth(client, user_auth_mock):
    twitch = fake_twitch(install_heat_ext=AsyncMock(side_effect=TwitchTokenExpiredError(error="expired")))
    with provide_overrides(install_heat, {"twitch": twitch}):
        resp = await client.get("/api/user/install-heat")

    assert resp.status_code == 400
    assert "Токен Twitch недействителен" in resp.json()["message"]


# ── update_settings ────────────────────────────────────────────────────────


async def test_update_settings_saves_fields(client, user_auth_mock, test_user, db_session):
    assert test_user.settings.enable_chat_bot is not True
    chat_bot = Mock(update_bot_channels=AsyncMock(), send_message=AsyncMock())
    twitch = fake_twitch()

    with provide_overrides(update_settings, {"chat_bot": chat_bot, "twitch": twitch}):
        resp = await client.post("/api/user/update_settings", data={"enable_chat_bot": "true", "enable_dice": "false"})

    assert resp.status_code == 200
    await db_session.refresh(test_user.settings)
    assert test_user.settings.enable_chat_bot is True
    assert test_user.settings.enable_dice is False
    chat_bot.update_bot_channels.assert_awaited_once()


async def test_update_settings_shoutout_conflict(client, user_auth_mock, test_user):
    chat_bot = Mock(update_bot_channels=AsyncMock())
    twitch = fake_twitch(subscribe_raid=AsyncMock(side_effect=http_error(409)))

    with provide_overrides(update_settings, {"chat_bot": chat_bot, "twitch": twitch}):
        resp = await client.post("/api/user/update_settings", data={"enable_shoutout_on_raid": "true"})

    assert resp.status_code == 409
    assert "уже существует" in resp.json()["message"]


async def test_update_settings_shoutout_disabled_unsubscribes(client, user_auth_mock, test_user):
    chat_bot = Mock(update_bot_channels=AsyncMock())
    twitch = fake_twitch()

    with provide_overrides(update_settings, {"chat_bot": chat_bot, "twitch": twitch}):
        resp = await client.post("/api/user/update_settings", data={"enable_shoutout_on_raid": "false"})

    assert resp.status_code == 200
    twitch.unsubscribe_raid.assert_awaited_once()


# ── memealerts: coins + deprecated setup ──────────────────────────────────


async def test_update_memealerts_coins(client, user_auth_mock, test_user, db_session):
    resp = await client.post("/api/user/memealerts/coins", json={"count": 7})

    assert resp.status_code == 200
    await db_session.refresh(test_user.memealerts)
    assert test_user.memealerts.coins_for_reward == 7


async def test_memealerts_coins_validation(client, user_auth_mock):
    resp = await client.post("/api/user/memealerts/coins", json={"count": 0})
    assert resp.status_code == 422


async def test_setup_memealert_no_change(client, user_auth_mock, test_user, db_session):
    test_user.memealerts.memealerts_reward = uuid.uuid4()
    await db_session.commit()

    resp = await client.post("/api/user/memealerts", params={"enable": "true"})

    assert resp.status_code == 208


async def test_setup_memealert_missing_key(client, user_auth_mock, test_user):
    resp = await client.post("/api/user/memealerts", params={"enable": "true"})

    assert resp.status_code == 422


async def test_setup_memealert_invalid_jwt(client, user_auth_mock, test_user, monkeypatch):
    monkeypatch.setattr(user_api, "token_expires_in_days", AsyncMock(side_effect=DecodeError))

    resp = await client.post("/api/user/memealerts", params={"enable": "true"}, data={"key": "garbage"})

    assert resp.status_code == 400
    assert "не является валидным" in resp.json()["message"]


async def test_setup_memealert_expired_token(client, user_auth_mock, test_user, monkeypatch):
    monkeypatch.setattr(user_api, "token_expires_in_days", AsyncMock(side_effect=MATokenExpiredError))

    resp = await client.post("/api/user/memealerts", params={"enable": "true"}, data={"key": "tok"})

    assert resp.status_code == 400


async def test_setup_memealert_auth_error(client, user_auth_mock, test_user, monkeypatch):
    monkeypatch.setattr(user_api, "token_expires_in_days", AsyncMock(return_value=5))
    memealerts = Mock(get_current=AsyncMock(side_effect=RuntimeError))
    twitch = fake_twitch()

    with provide_overrides(setup_memealert, {"twitch": twitch, "memealerts": memealerts}):
        resp = await client.post("/api/user/memealerts", params={"enable": "true"}, data={"key": "tok"})

    assert resp.status_code == 400
    twitch.create_reward.assert_not_awaited()


async def test_setup_memealert_refresh_updates_token(client, user_auth_mock, test_user, db_session, monkeypatch):
    monkeypatch.setattr(user_api, "token_expires_in_days", AsyncMock(return_value=5))
    memealerts = Mock(get_current=AsyncMock(return_value=Mock(channel=None)))
    twitch = fake_twitch()

    with provide_overrides(setup_memealert, {"twitch": twitch, "memealerts": memealerts}):
        resp = await client.post(
            "/api/user/memealerts", params={"enable": "true", "refresh": "true"}, data={"key": "new-tok"}
        )

    assert resp.status_code == 200
    await db_session.refresh(test_user.memealerts)
    assert test_user.memealerts.memealerts_token == "new-tok"
    twitch.create_reward.assert_not_awaited()


async def test_setup_memealert_enable_creates_reward(client, user_auth_mock, test_user, db_session, monkeypatch):
    monkeypatch.setattr(user_api, "token_expires_in_days", AsyncMock(return_value=5))
    memealerts = Mock(get_current=AsyncMock(return_value=Mock(channel=None)))
    twitch = fake_twitch()

    with provide_overrides(setup_memealert, {"twitch": twitch, "memealerts": memealerts}):
        resp = await client.post("/api/user/memealerts", params={"enable": "true"}, data={"key": "tok"})

    assert resp.status_code == 201
    await db_session.refresh(test_user.memealerts)
    assert test_user.memealerts.memealerts_reward == twitch.create_reward.return_value.id
    twitch.subscribe_reward.assert_awaited_once()


async def test_setup_memealert_duplicate_reward(client, user_auth_mock, test_user, monkeypatch):
    monkeypatch.setattr(user_api, "token_expires_in_days", AsyncMock(return_value=5))
    memealerts = Mock(get_current=AsyncMock(return_value=Mock(channel=None)))
    twitch = fake_twitch(
        create_reward=AsyncMock(side_effect=TwitchAPIException("CREATE_CUSTOM_REWARD_DUPLICATE_REWARD"))
    )

    with provide_overrides(setup_memealert, {"twitch": twitch, "memealerts": memealerts}):
        resp = await client.post("/api/user/memealerts", params={"enable": "true"}, data={"key": "tok"})

    assert resp.status_code == 400
    assert "уже существует" in resp.json()["message"]


async def test_setup_memealert_disable_deletes_reward(client, user_auth_mock, test_user, db_session):
    test_user.memealerts.memealerts_reward = uuid.uuid4()
    await db_session.commit()
    twitch = fake_twitch()

    with provide_overrides(setup_memealert, {"twitch": twitch}):
        resp = await client.post("/api/user/memealerts", params={"enable": "false"})

    assert resp.status_code == 200
    await db_session.refresh(test_user.memealerts)
    assert test_user.memealerts.memealerts_reward is None


async def test_setup_memealert_disable_missing_reward_ok(client, user_auth_mock, test_user, db_session):
    test_user.memealerts.memealerts_reward = uuid.uuid4()
    await db_session.commit()
    twitch = fake_twitch(delete_reward=AsyncMock(side_effect=TwitchResourceNotFound))

    with provide_overrides(setup_memealert, {"twitch": twitch}):
        resp = await client.post("/api/user/memealerts", params={"enable": "false"})

    assert resp.status_code == 200


# ── setup-ai-stickers ─────────────────────────────────────────────────────


async def test_setup_ai_stickers_already_enabled(client, user_auth_mock, test_user, db_session):
    test_user.settings.ai_sticker_reward_id = uuid.uuid4()
    await db_session.commit()
    twitch = fake_twitch()

    with provide_overrides(setup_ai_stickers, {"twitch": twitch}):
        resp = await client.post("/api/user/setup-ai-stickers", params={"enable": "true"})

    assert resp.status_code == 208
    twitch.create_reward.assert_not_awaited()


async def test_setup_ai_stickers_reward_lost_recreates(client, user_auth_mock, test_user, db_session):
    test_user.settings.ai_sticker_reward_id = uuid.uuid4()
    await db_session.commit()
    twitch = fake_twitch(validate_reward_subscription=AsyncMock(return_value="Награда не найдена"))

    with provide_overrides(setup_ai_stickers, {"twitch": twitch}):
        resp = await client.post("/api/user/setup-ai-stickers", params={"enable": "true"})

    assert resp.status_code == 201
    await db_session.refresh(test_user.settings)
    assert test_user.settings.ai_sticker_reward_id == twitch.create_reward.return_value.id
    twitch.subscribe_reward.assert_awaited_once()


async def test_setup_ai_stickers_restores_subscription(client, user_auth_mock, test_user, db_session):
    test_user.settings.ai_sticker_reward_id = uuid.uuid4()
    await db_session.commit()
    twitch = fake_twitch(validate_reward_subscription=AsyncMock(return_value="подписка сломана"))

    with provide_overrides(setup_ai_stickers, {"twitch": twitch}):
        resp = await client.post("/api/user/setup-ai-stickers", params={"enable": "true"})

    assert resp.status_code == 200
    twitch.subscribe_reward.assert_awaited_once()
    twitch.create_reward.assert_not_awaited()


async def test_setup_ai_stickers_creates_when_off(client, user_auth_mock, test_user, db_session):
    twitch = fake_twitch()

    with provide_overrides(setup_ai_stickers, {"twitch": twitch}):
        resp = await client.post("/api/user/setup-ai-stickers", params={"enable": "true"})

    assert resp.status_code == 201
    await db_session.refresh(test_user.settings)
    assert test_user.settings.ai_sticker_reward_id == twitch.create_reward.return_value.id


async def test_setup_ai_stickers_already_off(client, user_auth_mock, test_user):
    twitch = fake_twitch()

    with provide_overrides(setup_ai_stickers, {"twitch": twitch}):
        resp = await client.post("/api/user/setup-ai-stickers", params={"enable": "false"})

    assert resp.status_code == 208


async def test_setup_ai_stickers_disable(client, user_auth_mock, test_user, db_session):
    test_user.settings.ai_sticker_reward_id = uuid.uuid4()
    await db_session.commit()
    twitch = fake_twitch()

    with provide_overrides(setup_ai_stickers, {"twitch": twitch}):
        resp = await client.post("/api/user/setup-ai-stickers", params={"enable": "false"})

    assert resp.status_code == 200
    await db_session.refresh(test_user.settings)
    assert test_user.settings.ai_sticker_reward_id is None
    twitch.delete_reward.assert_awaited_once()


# ── ai-stickers/recent ────────────────────────────────────────────────────


async def test_recent_stickers_bad_before(client, user_auth_mock):
    resp = await client.get("/api/user/ai-stickers/recent", params={"before": "not-a-date"})
    assert resp.status_code == 400


async def test_recent_stickers_empty(client, user_auth_mock):
    resp = await client.get("/api/user/ai-stickers/recent")

    assert resp.status_code == 200


# ── reference upload ──────────────────────────────────────────────────────


async def test_reference_description_only(client, user_auth_mock, test_user, db_session):
    s3 = Mock(put_object=AsyncMock(), delete_object=AsyncMock())
    factory = lambda: SessionCtx(db_session)  # noqa: E731

    with provide_overrides(upload_reference, {"s3": s3, "db_session_factory": factory}):
        resp = await client.post("/api/user/reference", data={"description": "внешность героя"})

    assert resp.status_code == 200
    from database.models import CharacterInfo

    row = await db_session.scalar(
        __import__("sqlalchemy").select(CharacterInfo).where(CharacterInfo.name == test_user.login_name)
    )
    assert row is not None
    assert row.description == "внешность героя"
    s3.put_object.assert_not_awaited()


async def test_reference_requires_file_or_description(client, user_auth_mock, test_user):
    resp = await client.post("/api/user/reference", data={})
    assert resp.status_code == 400


async def test_reference_custom_name_forbidden(client, user_auth_mock, test_user):
    resp = await client.post("/api/user/reference", params={"name": "someone_else"}, data={"description": "x"})
    assert resp.status_code == 403


async def test_reference_png_upload(client, user_auth_mock, test_user, db_session):
    s3 = Mock(put_object=AsyncMock(), delete_object=AsyncMock())
    factory = lambda: SessionCtx(db_session)  # noqa: E731

    with provide_overrides(upload_reference, {"s3": s3, "db_session_factory": factory}):
        resp = await client.post(
            "/api/user/reference",
            files={"file": ("ref.png", PNG_BYTES, "image/png")},
            data={"description": "портрет"},
        )

    assert resp.status_code == 200
    s3.put_object.assert_awaited_once()
    assert s3.put_object.await_args.args[0].endswith(".png")
    s3.delete_object.assert_not_awaited()


async def test_reference_bad_png_magic(client, user_auth_mock, test_user, db_session):
    s3 = Mock(put_object=AsyncMock())
    factory = lambda: SessionCtx(db_session)  # noqa: E731

    with provide_overrides(upload_reference, {"s3": s3, "db_session_factory": factory}):
        resp = await client.post(
            "/api/user/reference",
            files={"file": ("ref.png", b"JFIF--not-png" + b"0" * 32, "image/png")},
        )

    assert resp.status_code == 415


async def test_reference_wrong_content_type(client, user_auth_mock, test_user, db_session):
    s3 = Mock(put_object=AsyncMock())
    factory = lambda: SessionCtx(db_session)  # noqa: E731

    with provide_overrides(upload_reference, {"s3": s3, "db_session_factory": factory}):
        resp = await client.post(
            "/api/user/reference",
            files={"file": ("ref.jpg", PNG_BYTES, "image/jpeg")},
        )

    assert resp.status_code == 415


async def test_reference_too_large(client, user_auth_mock, test_user, db_session):
    s3 = Mock(put_object=AsyncMock())
    factory = lambda: SessionCtx(db_session)  # noqa: E731
    big = PNG_BYTES + b"0" * (10_000_001 - len(PNG_BYTES))

    with provide_overrides(upload_reference, {"s3": s3, "db_session_factory": factory}):
        resp = await client.post(
            "/api/user/reference",
            files={"file": ("ref.png", big, "image/png")},
        )

    assert resp.status_code == 413


# ── TTS ───────────────────────────────────────────────────────────────────


async def test_setup_tts_creates_reward(client, user_auth_mock, test_user, db_session):
    twitch = fake_twitch()

    with provide_overrides(setup_tts, {"twitch": twitch}):
        resp = await client.post("/api/user/setup-tts", params={"enable": "true"})

    assert resp.status_code == 201
    await reload_tts(db_session, test_user)
    assert test_user.tts is not None
    assert test_user.tts.tts_reward_id == twitch.create_reward.return_value.id


async def test_setup_tts_already_enabled(client, user_auth_mock, test_user, db_session):
    twitch = fake_twitch()
    with provide_overrides(setup_tts, {"twitch": twitch}):
        await client.post("/api/user/setup-tts", params={"enable": "true"})

        resp = await client.post("/api/user/setup-tts", params={"enable": "true"})

    assert resp.status_code == 208
    assert twitch.create_reward.await_count == 1


async def test_setup_tts_restores_subscription(client, user_auth_mock, test_user, db_session):
    twitch = fake_twitch()
    with provide_overrides(setup_tts, {"twitch": twitch}):
        await client.post("/api/user/setup-tts", params={"enable": "true"})
        twitch.validate_reward_subscription = AsyncMock(return_value="подписка сломана")

        resp = await client.post("/api/user/setup-tts", params={"enable": "true"})

    assert resp.status_code == 200
    twitch.update_reward.assert_awaited_once()


async def test_setup_tts_disable(client, user_auth_mock, test_user, db_session):
    twitch = fake_twitch()
    with provide_overrides(setup_tts, {"twitch": twitch}):
        await client.post("/api/user/setup-tts", params={"enable": "true"})

        resp = await client.post("/api/user/setup-tts", params={"enable": "false"})

    assert resp.status_code == 200
    await reload_tts(db_session, test_user)
    assert test_user.tts.tts_reward_id is None
    twitch.delete_reward.assert_awaited_once()


async def test_update_tts_settings(client, user_auth_mock, test_user, db_session):
    with provide_overrides(setup_tts, {"twitch": fake_twitch()}):
        await client.post("/api/user/setup-tts", params={"enable": "true"})

    resp = await client.post(
        "/api/user/tts/settings",
        json={"enabled": False, "max_length": 300, "model": "voice-2"},
    )

    assert resp.status_code == 200
    await reload_tts(db_session, test_user)
    assert test_user.tts.enabled is False
    assert test_user.tts.max_length == 300
    assert test_user.tts.model == "voice-2"


async def test_update_tts_settings_validation(client, user_auth_mock):
    resp = await client.post("/api/user/tts/settings", json={"max_length": 1})
    assert resp.status_code == 422


async def test_update_tts_permissions(client, user_auth_mock, test_user, db_session):
    with provide_overrides(setup_tts, {"twitch": fake_twitch()}):
        await client.post("/api/user/setup-tts", params={"enable": "true"})

    resp = await client.post(
        "/api/user/tts/permissions",
        json={"roles": {"subscriber": {"command": False}}},
    )

    assert resp.status_code == 200
    await reload_tts(db_session, test_user)
    assert test_user.tts.permissions["roles"]["subscriber"]["command"] is False


async def test_reset_tts_key(client, user_auth_mock, test_user, db_session):
    with provide_overrides(setup_tts, {"twitch": fake_twitch()}):
        await client.post("/api/user/setup-tts", params={"enable": "true"})

    resp = await client.post("/api/user/tts/reset-key")

    assert resp.status_code == 200
    assert len(resp.json()["key"]) == 32


async def test_delete_tts_key(client, user_auth_mock, test_user, db_session):
    with provide_overrides(setup_tts, {"twitch": fake_twitch()}):
        await client.post("/api/user/setup-tts", params={"enable": "true"})

    assert (await client.post("/api/user/tts/reset-key")).status_code == 200
    resp = await client.post("/api/user/tts/delete-key")

    assert resp.status_code == 200
    await reload_tts(db_session, test_user)
    assert test_user.tts.external_key is None


async def reload_tts(db_session, test_user):
    """ensure_tts_settings пишет строку через pg_insert — relationship надо перечитать."""
    await db_session.refresh(test_user, attribute_names=["tts"])


async def prepare_tts_key(client, test_user, db_session, enabled=True) -> str:
    with provide_overrides(setup_tts, {"twitch": fake_twitch()}):
        await client.post("/api/user/setup-tts", params={"enable": "true"})
    await reload_tts(db_session, test_user)
    test_user.tts.enabled = enabled
    test_user.tts.external_key = "ext-secret-key"
    await db_session.commit()
    return "ext-secret-key"


async def test_tts_external_invalid_key(client):
    resp = await client.post("/api/user/tts/wrong-key", json={"input": "привет"})
    assert resp.status_code == 403


async def test_tts_external_disabled_key(client, user_auth_mock, test_user, db_session):
    key = await prepare_tts_key(client, test_user, db_session, enabled=False)
    resp = await client.post(f"/api/user/tts/{key}", json={"input": "привет"})
    assert resp.status_code == 403


async def test_tts_external_moderation_blocked(client, user_auth_mock, test_user, db_session):
    key = await prepare_tts_key(client, test_user, db_session)
    moderation = Mock(validate=Mock(return_value=Mock(is_banned=True, found_word="запрещёнка")))
    ssem = Mock(broadcast=AsyncMock())

    with provide_overrides(tts_external_speech, {"ssem": ssem, "moderation": moderation}):
        resp = await client.post(f"/api/user/tts/{key}", json={"input": "запрещёнка"})

    assert resp.status_code == 422
    assert "запрещёнка" in resp.json()["detail"]
    ssem.broadcast.assert_not_awaited()


async def test_tts_external_broadcasts(client, user_auth_mock, test_user, db_session):
    key = await prepare_tts_key(client, test_user, db_session)
    moderation = Mock(validate=Mock(return_value=Mock(is_banned=False, found_word=None)))
    ssem = Mock(broadcast=AsyncMock())

    with provide_overrides(tts_external_speech, {"ssem": ssem, "moderation": moderation}):
        resp = await client.post(f"/api/user/tts/{key}", params={"moderate": "false"}, json={"input": "привет"})

    assert resp.status_code == 200
    moderation.validate.assert_not_called()  # moderate=false — модерация пропущена
    ssem.broadcast.assert_awaited_once()
    args = ssem.broadcast.await_args.args
    assert args[1] == SSEChannel.TTS
    import json as json_lib

    payload = json_lib.loads(args[2])
    assert payload["text"] == "привет"
