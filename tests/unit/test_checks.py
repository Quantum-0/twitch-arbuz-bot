# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Роут-тесты /api/user/check/*: SSE/Heat/Memealerts/награды/TTS/TG-чеки."""

import inspect
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from memealerts.types.exceptions import MATokenExpiredError

import routers.api.user.checks as checks_mod
from exceptions import MAInvalidTokenError, MANoToken, MATokenRefreshError, MAUnavailableError, MAValidationRespError
from main import app
from schemas.memealerts import MAChannel, MAUserInfo
from services.twitch_token_service import TwitchTokenExpiredError


def _make_override(fake):
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


class FakeHttpResponse:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None, bad_json: bool = False):
        self.status_code = status_code
        self._body = body or {}
        self._bad_json = bad_json

    def json(self) -> dict[str, Any]:
        if self._bad_json:
            raise ValueError("not json")
        return self._body


class FakeHttpRouter:
    """Фейк httpx.AsyncClient: маршрутизация GET/POST по URL с настраиваемыми ответами."""

    def __init__(self) -> None:
        self.get_responses: dict[str, Any] = {}
        self.post_responses: dict[str, Any] = {}
        self.posts: list[dict[str, Any]] = []

    def on_get(self, url_part: str, response: Any) -> None:
        self.get_responses[url_part] = response

    def on_post(self, url_part: str, response: Any) -> None:
        self.post_responses[url_part] = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def get(self, url: str, **kwargs: Any) -> Any:  # noqa: ARG002
        for part, resp in self.get_responses.items():
            if part in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return FakeHttpResponse(404)

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.posts.append({"url": url, **kwargs})
        for part, resp in self.post_responses.items():
            if part in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return FakeHttpResponse(404)


@pytest.fixture
def http_router(monkeypatch) -> FakeHttpRouter:
    router = FakeHttpRouter()
    monkeypatch.setattr(checks_mod.httpx, "AsyncClient", lambda *a, **kw: router)  # noqa: ARG005
    return router


@pytest.fixture
async def client(session_override) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sse_manager():
    return SimpleNamespace(has_clients=AsyncMock(return_value=True))


@pytest.fixture
def twitch_fake():
    return SimpleNamespace(
        get_user_active_ext=AsyncMock(),
        validate_reward_subscription=AsyncMock(return_value=[]),
        unsubscribe_stream_online=AsyncMock(),
        unsubscribe_stream_offline=AsyncMock(),
    )


def make_ext(active: bool = True, ext_id: str = "cr20njfkgll4okyrhag7xxph270sqk") -> SimpleNamespace:
    return SimpleNamespace(overlay={"1": SimpleNamespace(active=active, id=ext_id)})


class TestSseCheck:
    async def test_connected(self, client, user_auth_mock, sse_manager):
        route = checks_mod.check_user_sse_connected
        with provide_overrides(route, {"ssem": sse_manager}):
            resp = await client.get("/api/user/check/sse")
        assert resp.json() == {"result": True, "problems": [], "warnings": []}

    async def test_not_connected(self, client, user_auth_mock, sse_manager):
        sse_manager.has_clients = AsyncMock(return_value=False)
        route = checks_mod.check_user_sse_connected
        with provide_overrides(route, {"ssem": sse_manager}):
            resp = await client.get("/api/user/check/sse")
        body = resp.json()
        assert body["result"] is False
        assert "OBS не открыт" in body["problems"][0]

    async def test_with_channel_param(self, client, user_auth_mock, sse_manager):
        route = checks_mod.check_user_sse_connected
        with provide_overrides(route, {"ssem": sse_manager}):
            await client.get("/api/user/check/sse", params={"channel": "heat"})
        sse_manager.has_clients.assert_awaited_once_with(123456, "heat")


class TestHeatInstalled:
    async def test_ok(self, client, user_auth_mock, twitch_fake):
        twitch_fake.get_user_active_ext = AsyncMock(return_value=make_ext())
        with provide_overrides(checks_mod.check_heat_installed, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/heat-installed")
        assert resp.json()["result"] is True

    async def test_reauth_problem(self, client, user_auth_mock, twitch_fake):
        twitch_fake.get_user_active_ext = AsyncMock(side_effect=TwitchTokenExpiredError("expired"))
        with provide_overrides(checks_mod.check_heat_installed, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/heat-installed")
        assert resp.json()["problems"] == [checks_mod.REAUTH_PROBLEM]

    async def test_not_installed(self, client, user_auth_mock, twitch_fake):
        twitch_fake.get_user_active_ext = AsyncMock(return_value=SimpleNamespace(overlay={}))
        with provide_overrides(checks_mod.check_heat_installed, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/heat-installed")
        assert "не установлен" in resp.json()["problems"][0]

    async def test_not_active(self, client, user_auth_mock, twitch_fake):
        twitch_fake.get_user_active_ext = AsyncMock(return_value=make_ext(active=False))
        with provide_overrides(checks_mod.check_heat_installed, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/heat-installed")
        assert "не активно" in resp.json()["problems"][0]

    async def test_other_extension(self, client, user_auth_mock, twitch_fake):
        twitch_fake.get_user_active_ext = AsyncMock(return_value=make_ext(ext_id="other-ext"))
        with provide_overrides(checks_mod.check_heat_installed, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/heat-installed")
        assert "другое расширение" in resp.json()["problems"][0]


class TestMemealertsToken:
    @staticmethod
    def ma_user_info(**channel_kwargs) -> MAUserInfo:
        return MAUserInfo(
            id="a" * 24,
            code="user",
            name="user",
            avatar="https://ma.example.com/a.png",
            email="u@example.com",
            channel=MAChannel(**channel_kwargs),
        )

    async def run_check(self, client, auth_fake, api_fake) -> dict:
        with provide_overrides(
            checks_mod.check_memealerts_token, {"memealerts_auth": auth_fake, "memealerts_api": api_fake}
        ):
            resp = await client.get("/api/user/check/memealerts-token")
        assert resp.status_code == 200
        return resp.json()

    async def test_ok(self, client, user_auth_mock):
        auth = SimpleNamespace(get_token_of_user=AsyncMock(return_value="tok"))
        api = SimpleNamespace(get_user_info=AsyncMock(return_value=self.ma_user_info()))
        body = await self.run_check(client, auth, api)
        assert body["result"] is True
        assert body["warnings"] == []

    @pytest.mark.parametrize(
        ("exc", "problem_part"),
        [
            (MANoToken, "Токен OAuth отсутствует"),
            (MATokenExpiredError("expired"), "Токен невалидный"),
            (MAInvalidTokenError, "Токен невалидный"),
            (httpx.ConnectError("boom"), "Ошибка подключения к Memealerts"),
            (RuntimeError("unexpected"), "Неизвестная ошибка"),
        ],
    )
    async def test_token_failures(self, client, user_auth_mock, exc, problem_part):
        auth = SimpleNamespace(get_token_of_user=AsyncMock(side_effect=exc))
        api = SimpleNamespace(get_user_info=AsyncMock())
        body = await self.run_check(client, auth, api)
        assert problem_part in body["problems"][0]

    async def test_token_refresh_error(self, client, user_auth_mock):
        auth = SimpleNamespace(get_token_of_user=AsyncMock(side_effect=MATokenRefreshError("rate limited")))
        api = SimpleNamespace(get_user_info=AsyncMock())
        body = await self.run_check(client, auth, api)
        assert "rate limited" in body["problems"][0]

    @pytest.mark.parametrize(
        ("exc", "problem_part"),
        [
            (MAUnavailableError, "Ошибка подключения к Memealerts"),
            (MAInvalidTokenError, "Ошибка авторизации"),
            (MAValidationRespError, "Ошибка формирования ответа"),
        ],
    )
    async def test_user_info_failures(self, client, user_auth_mock, exc, problem_part):
        auth = SimpleNamespace(get_token_of_user=AsyncMock(return_value="tok"))
        api = SimpleNamespace(get_user_info=AsyncMock(side_effect=exc))
        body = await self.run_check(client, auth, api)
        assert problem_part in body["problems"][0]

    async def test_channel_warnings(self, client, user_auth_mock):
        auth = SimpleNamespace(get_token_of_user=AsyncMock(return_value="tok"))
        api = SimpleNamespace(
            get_user_info=AsyncMock(return_value=self.ma_user_info(welcome_bonus_enabled=False, disable_stickers=True))
        )
        body = await self.run_check(client, auth, api)
        assert body["result"] is True
        assert len(body["warnings"]) == 2


class TestRewardChecks:
    async def test_reward_response_states(self):
        assert checks_mod._reward_check_response([]).state == "ok"
        assert checks_mod._reward_check_response(["Награда не найдена"]).state == "missing"
        assert checks_mod._reward_check_response(["Что-то сломалось"]).state == "broken"

    async def test_memealerts_reward_ok(self, client, user_auth_mock, twitch_fake, db_session, test_user):
        test_user.memealerts.memealerts_reward = uuid4()
        await db_session.commit()
        with provide_overrides(checks_mod.check_memealerts_reward, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/memealerts-reward")
        assert resp.json()["state"] == "ok"
        twitch_fake.validate_reward_subscription.assert_awaited_once()

    async def test_memealerts_reward_reauth(self, client, user_auth_mock, twitch_fake):
        twitch_fake.validate_reward_subscription = AsyncMock(side_effect=TwitchTokenExpiredError("expired"))
        with provide_overrides(checks_mod.check_memealerts_reward, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/memealerts-reward")
        assert resp.json()["problems"] == [checks_mod.REAUTH_PROBLEM]

    async def test_ai_stickers_no_reward(self, client, user_auth_mock, twitch_fake):
        with provide_overrides(checks_mod.check_ai_stickers_reward, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/ai-stickers-reward")
        body = resp.json()
        # «Награда не создана» не совпадает с «Награда не найдена» → state=broken
        assert body["state"] == "broken"
        assert body["problems"] == ["Награда не создана"]

    async def test_ai_stickers_reward_ok(self, client, user_auth_mock, twitch_fake, db_session, test_user):
        test_user.settings.ai_sticker_reward_id = uuid4()
        await db_session.commit()
        with provide_overrides(checks_mod.check_ai_stickers_reward, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/ai-stickers-reward")
        assert resp.json()["state"] == "ok"

    async def test_tts_reward_no_reward(self, client, user_auth_mock, twitch_fake):
        with provide_overrides(checks_mod.check_tts_reward, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/tts-reward")
        assert resp.json()["problems"] == ["Награда не создана"]

    async def test_tts_reward_ok(self, client, user_auth_mock, twitch_fake, db_session, test_user):
        from database.models import TTSSettings

        db_session.add(TTSSettings(user_id=test_user.id, tts_reward_id=uuid4()))
        await db_session.commit()
        await db_session.refresh(test_user, attribute_names=["tts"])
        with provide_overrides(checks_mod.check_tts_reward, {"twitch": twitch_fake}):
            resp = await client.get("/api/user/check/tts-reward")
        assert resp.json()["state"] == "ok"


class TestTtsOverlay:
    async def test_connected(self, client, user_auth_mock, sse_manager):
        with provide_overrides(checks_mod.check_tts_overlay, {"ssem": sse_manager}):
            resp = await client.get("/api/user/check/tts-overlay")
        assert resp.json()["result"] is True
        sse_manager.has_clients.assert_awaited_once_with(123456, "tts")

    async def test_not_connected(self, client, user_auth_mock, sse_manager):
        sse_manager.has_clients = AsyncMock(return_value=False)
        with provide_overrides(checks_mod.check_tts_overlay, {"ssem": sse_manager}):
            resp = await client.get("/api/user/check/tts-overlay")
        assert "TTS-оверлей не подключён" in resp.json()["problems"][0]


class TestTtsServer:
    PATH = "/api/user/check/tts-server"

    async def test_ok(self, client, http_router):
        http_router.on_get("/health", FakeHttpResponse(200, {"status": "ok", "rvc_available": True}))
        resp = await client.get(self.PATH)
        assert resp.json()["result"] is True

    async def test_unreachable(self, client, http_router):
        http_router.on_get("/health", httpx.ConnectError("boom"))
        resp = await client.get(self.PATH)
        assert resp.json()["problems"] == ["TTS-сервер недоступен"]

    async def test_bad_status_code(self, client, http_router):
        http_router.on_get("/health", FakeHttpResponse(503))
        resp = await client.get(self.PATH)
        assert "вернул 503" in resp.json()["problems"][0]

    async def test_bad_json(self, client, http_router):
        http_router.on_get("/health", FakeHttpResponse(200, bad_json=True))
        resp = await client.get(self.PATH)
        assert "некорректный ответ" in resp.json()["problems"][0]

    async def test_status_error(self, client, http_router):
        http_router.on_get("/health", FakeHttpResponse(200, {"status": "error"}))
        resp = await client.get(self.PATH)
        assert "сообщил об ошибке" in resp.json()["problems"][0]

    async def test_rvc_unavailable(self, client, http_router):
        http_router.on_get("/health", FakeHttpResponse(200, {"status": "ok", "rvc_available": False}))
        resp = await client.get(self.PATH)
        assert "RVC-сервер недоступен" in resp.json()["problems"][0]


class TestTelegram:
    PATH = "/api/user/check/telegram"

    @staticmethod
    def chat_status(member: bool = True, can_post: bool = True) -> FakeHttpResponse:
        return FakeHttpResponse(200, {"member": member, "can_post": can_post})

    async def test_service_down(self, client, user_auth_mock, twitch_fake, http_router):
        http_router.on_get("/api/healthcheck", httpx.ConnectError("boom"))
        with provide_overrides(checks_mod.check_telegram, {"twitch": twitch_fake}):
            resp = await client.get(self.PATH)
        assert resp.json()["problems"] == ["TG-сервис недоступен"]

    async def test_service_bad_code(self, client, user_auth_mock, twitch_fake, http_router):
        http_router.on_get("/api/healthcheck", FakeHttpResponse(500))
        with provide_overrides(checks_mod.check_telegram, {"twitch": twitch_fake}):
            resp = await client.get(self.PATH)
        assert "вернул 500" in resp.json()["problems"][0]

    async def test_no_bindings_ok(self, client, user_auth_mock, twitch_fake, http_router):
        http_router.on_get("/api/healthcheck", FakeHttpResponse(200))
        with provide_overrides(checks_mod.check_telegram, {"twitch": twitch_fake}):
            resp = await client.get(self.PATH)
        assert resp.json() == {"result": True, "problems": [], "warnings": []}

    async def test_all_chats_ok(self, client, user_auth_mock, twitch_fake, http_router, db_session, test_user):
        from database.models import TelegramSettings

        db_session.add(
            TelegramSettings(
                user_id=test_user.id,
                stream_chat_id="-1001",
                clips_chat_id="-1002",
                stickers_chat_id="-1003",
            )
        )
        await db_session.commit()
        await db_session.refresh(test_user, attribute_names=["telegram"])
        http_router.on_get("/api/healthcheck", FakeHttpResponse(200))
        http_router.on_post("/api/chat_status", self.chat_status())

        with provide_overrides(checks_mod.check_telegram, {"twitch": twitch_fake}):
            resp = await client.get(self.PATH)

        assert resp.json()["result"] is True
        assert len(http_router.posts) == 3  # stream + clips + stickers

    async def test_kicked_from_stream_chat_resets_binding(
        self, client, user_auth_mock, twitch_fake, http_router, db_session, test_user
    ):
        from database.models import TelegramSettings

        tg = TelegramSettings(user_id=test_user.id, stream_chat_id="-1001", stream_notification_enabled=True)
        db_session.add(tg)
        await db_session.commit()
        await db_session.refresh(test_user, attribute_names=["telegram"])
        http_router.on_get("/api/healthcheck", FakeHttpResponse(200))
        http_router.on_post("/api/chat_status", self.chat_status(member=False))

        with provide_overrides(checks_mod.check_telegram, {"twitch": twitch_fake}):
            resp = await client.get(self.PATH)

        body = resp.json()
        assert body["result"] is False
        assert any("привязка очищена" in p for p in body["problems"])
        twitch_fake.unsubscribe_stream_online.assert_awaited_once()
        twitch_fake.unsubscribe_stream_offline.assert_awaited_once()

        await db_session.refresh(tg)
        assert tg.stream_chat_id is None
        assert tg.stream_notification_enabled is False

    async def test_cannot_post_problem(self, client, user_auth_mock, twitch_fake, http_router, db_session, test_user):
        from database.models import TelegramSettings

        db_session.add(TelegramSettings(user_id=test_user.id, clips_chat_id="-1002"))
        await db_session.commit()
        await db_session.refresh(test_user, attribute_names=["telegram"])
        http_router.on_get("/api/healthcheck", FakeHttpResponse(200))
        http_router.on_post("/api/chat_status", self.chat_status(member=True, can_post=False))

        with provide_overrides(checks_mod.check_telegram, {"twitch": twitch_fake}):
            resp = await client.get(self.PATH)

        assert any("не может отправлять" in p for p in resp.json()["problems"])

    async def test_chat_status_bad_code(self, client, user_auth_mock, twitch_fake, http_router, db_session, test_user):
        from database.models import TelegramSettings

        db_session.add(TelegramSettings(user_id=test_user.id, clips_chat_id="-1002"))
        await db_session.commit()
        await db_session.refresh(test_user, attribute_names=["telegram"])
        http_router.on_get("/api/healthcheck", FakeHttpResponse(200))
        http_router.on_post("/api/chat_status", FakeHttpResponse(502))

        with provide_overrides(checks_mod.check_telegram, {"twitch": twitch_fake}):
            resp = await client.get(self.PATH)

        assert any("код 502" in p for p in resp.json()["problems"])

    async def test_chat_status_http_error(
        self, client, user_auth_mock, twitch_fake, http_router, db_session, test_user
    ):
        from database.models import TelegramSettings

        db_session.add(TelegramSettings(user_id=test_user.id, clips_chat_id="-1002"))
        await db_session.commit()
        await db_session.refresh(test_user, attribute_names=["telegram"])
        http_router.on_get("/api/healthcheck", FakeHttpResponse(200))
        http_router.on_post("/api/chat_status", httpx.ConnectError("boom"))

        with provide_overrides(checks_mod.check_telegram, {"twitch": twitch_fake}):
            resp = await client.get(self.PATH)

        assert any("TG-сервис недоступен" in p for p in resp.json()["problems"])

    async def test_reset_scope_binding_helper(self, db_session, test_user):
        from database.models import TelegramSettings

        tg = TelegramSettings(
            user_id=test_user.id,
            stream_chat_id="-1",
            stream_chat_title="T",
            stream_notification_enabled=True,
            clips_chat_id="-2",
            clips_enabled=True,
            stickers_chat_id="-3",
            stickers_enabled=True,
        )
        db_session.add(tg)
        await db_session.flush()

        checks_mod._reset_scope_binding(tg, "stream")
        assert tg.stream_chat_id is None
        assert tg.stream_notification_enabled is False
        assert tg.clips_chat_id == "-2"  # другие scope не тронуты

        checks_mod._reset_scope_binding(tg, "clips")
        assert tg.clips_chat_id is None
        assert tg.clips_enabled is False

        checks_mod._reset_scope_binding(tg, "stickers")
        assert tg.stickers_chat_id is None
        assert tg.stickers_enabled is False
