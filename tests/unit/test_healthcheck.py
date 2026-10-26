# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты healthcheck-проверок: статусы, таймауты, HTTP-проверки и агрегация."""

import asyncio
from typing import Any

import pytest
from pydantic import SecretStr

import services.healthcheck as hc
from services.healthcheck import _run_check, check_db, check_mqtt, check_redis, run_healthchecks
from services.mqtt import MQTTClient
from tests.unit.fixtures.fake_redis import FakeRedis, SessionCtx


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict[str, Any]:
        return self._body


class FakeAsyncClient:
    response: FakeResponse = FakeResponse()
    requested: list[str] = []

    def __init__(self, timeout: float | None = None) -> None:  # noqa: ARG002
        pass

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:  # noqa: ARG002
        FakeAsyncClient.requested.append(url)
        return FakeAsyncClient.response


@pytest.fixture
def http_mock(monkeypatch):
    FakeAsyncClient.requested = []
    monkeypatch.setattr(hc.httpx, "AsyncClient", FakeAsyncClient)
    return FakeAsyncClient


class TestRunCheck:
    async def test_ok(self):
        async def check():
            return "fine"

        result = await _run_check("x", check)
        assert result.status == "ok"
        assert result.detail == "fine"
        assert result.latency_ms >= 0

    async def test_exception(self):
        async def check():
            raise ConnectionError("boom")

        result = await _run_check("x", check)
        assert result.status == "error"
        assert result.error == "ConnectionError: boom"
        assert result.detail is None

    async def test_timeout(self, monkeypatch):
        monkeypatch.setattr(hc, "CHECK_TIMEOUT_S", 0.05)

        async def check():
            await asyncio.sleep(1)
            return "late"  # pragma: no cover

        result = await _run_check("x", check)
        assert result.status == "error"
        assert result.error == "таймаут 0с"


class TestInfraChecks:
    async def test_check_db(self, db_session):
        assert await check_db(lambda: SessionCtx(db_session)) == "SELECT 1 — ok"

    async def test_check_redis_ok(self):
        assert await check_redis(FakeRedis()) == "PING — ok"

    async def test_check_redis_pong_lost(self):
        class NoPongRedis(FakeRedis):
            async def ping(self) -> bool:
                return False

        with pytest.raises(ConnectionError, match="PING не вернул"):
            await check_redis(NoPongRedis())

    @pytest.mark.parametrize(
        ("alive", "should_raise"),
        [(True, False), (False, True)],
    )
    async def test_check_mqtt(self, alive, should_raise):
        class FakeMQTT:
            is_alive = alive

        if should_raise:
            with pytest.raises(ConnectionError, match="нет подключения"):
                await check_mqtt(FakeMQTT())  # type: ignore[arg-type]
        else:
            assert "жив" in await check_mqtt(FakeMQTT())  # type: ignore[arg-type]

    async def test_check_mqtt_real_client_not_connected(self):
        assert not MQTTClient().is_alive


class TestHttpChecks:
    async def test_check_tts_ok(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.tts, "api_url", "https://tts.example.com/api/tts")
        http_mock.response = FakeResponse(200, {"status": "ok", "rvc_available": True})

        assert await hc.check_tts() == "health: ok, RVC доступен"
        assert http_mock.requested == ["https://tts.example.com/health"]

    async def test_check_tts_not_configured(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.tts, "api_url", "")
        with pytest.raises(RuntimeError, match="TTS не настроен"):
            await hc.check_tts()

    async def test_check_tts_http_error(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.tts, "api_url", "https://tts.example.com/api/tts")
        http_mock.response = FakeResponse(503)
        with pytest.raises(RuntimeError, match="вернул 503"):
            await hc.check_tts()

    async def test_check_tts_bad_status(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.tts, "api_url", "https://tts.example.com/api/tts")
        http_mock.response = FakeResponse(200, {"status": "error"})
        with pytest.raises(RuntimeError, match="сообщил об ошибке"):
            await hc.check_tts()

    async def test_check_tts_rvc_unavailable(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.tts, "api_url", "https://tts.example.com/api/tts")
        http_mock.response = FakeResponse(200, {"status": "ok", "rvc_available": False})
        with pytest.raises(RuntimeError, match="RVC-сервер недоступен"):
            await hc.check_tts()

    async def test_check_telegram_ok(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.telegram, "service_url", "https://tg.example.com")
        http_mock.response = FakeResponse(200)
        assert await hc.check_telegram() == "healthcheck: 200"
        assert http_mock.requested == ["https://tg.example.com/api/healthcheck"]

    async def test_check_telegram_failed(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.telegram, "service_url", "https://tg.example.com")
        http_mock.response = FakeResponse(500)
        with pytest.raises(RuntimeError, match="вернул 500"):
            await hc.check_telegram()

    async def test_check_twitch_ok(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.twitch, "bot_access_token", SecretStr("bot-token"))
        http_mock.response = FakeResponse(200, {"login": "twibot"})
        assert await hc.check_twitch() == "токен бота валиден (twibot)"

    async def test_check_twitch_invalid_token(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.twitch, "bot_access_token", SecretStr("bad"))
        http_mock.response = FakeResponse(401)
        with pytest.raises(RuntimeError, match="невалиден"):
            await hc.check_twitch()

    async def test_check_twitch_unexpected_code(self, http_mock, monkeypatch):
        monkeypatch.setattr(hc.settings.twitch, "bot_access_token", SecretStr("t"))
        http_mock.response = FakeResponse(502)
        with pytest.raises(RuntimeError, match="вернул 502"):
            await hc.check_twitch()


class TestRunHealthchecks:
    async def test_all_ok(self, monkeypatch):
        async def ok(*args: Any, **kwargs: Any):
            return "fine"

        monkeypatch.setattr(hc, "check_db", ok)
        monkeypatch.setattr(hc, "check_redis", ok)
        monkeypatch.setattr(hc, "check_mqtt", ok)
        monkeypatch.setattr(hc, "check_tts", ok)
        monkeypatch.setattr(hc, "check_telegram", ok)
        monkeypatch.setattr(hc, "check_twitch", ok)

        result = await run_healthchecks(lambda: None, FakeRedis(), None)  # type: ignore[arg-type]

        assert result.overall == "ok"
        assert set(result.services) == {"db", "redis", "mqtt", "tts", "telegram", "twitch"}
        assert all(s.status == "ok" for s in result.services.values())

    async def test_failure_makes_overall_error(self, monkeypatch):
        async def ok(*args: Any, **kwargs: Any):
            return "fine"

        async def broken(*args: Any, **kwargs: Any):
            raise RuntimeError("nope")

        monkeypatch.setattr(hc, "check_db", ok)
        monkeypatch.setattr(hc, "check_redis", broken)
        monkeypatch.setattr(hc, "check_mqtt", ok)
        monkeypatch.setattr(hc, "check_tts", ok)
        monkeypatch.setattr(hc, "check_telegram", ok)
        monkeypatch.setattr(hc, "check_twitch", ok)

        result = await run_healthchecks(lambda: None, FakeRedis(), None)  # type: ignore[arg-type]

        assert result.overall == "error"
        assert result.services["redis"].status == "error"
        assert result.services["redis"].error == "RuntimeError: nope"
        assert result.services["db"].status == "ok"
