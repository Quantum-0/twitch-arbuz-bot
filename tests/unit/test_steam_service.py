# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты services/steam.py: OpenID-флоу и GetPlayerSummaries."""

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import services.steam as steam_mod
from config import settings
from schemas.steam import SteamPlayerSummary
from services.steam import (
    STEAM_OPENID_ENDPOINT,
    STEAM_SUMMARIES_CHUNK_SIZE,
    SteamService,
)

STEAM_ID = "76561198073593788"


class FakeResp:
    def __init__(self, text: str = "", json_body: Any = None, status_exc: Exception | None = None):
        self.text = text
        self._json = json_body
        self._status_exc = status_exc

    def raise_for_status(self) -> None:
        if self._status_exc:
            raise self._status_exc

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("not json")
        return self._json


class FakeSteamClient:
    """Скриптованный httpx.AsyncClient: очередь ответов + ответы по подстроке URL."""

    responses: list[tuple[str, Any]] = []
    queue: list[Any] = []
    requests: list[dict[str, Any]] = []

    def _respond(self, url: str, **kwargs: Any) -> Any:
        self.requests.append({"url": url, **kwargs})
        if self.queue:
            resp = self.queue.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        for part, resp in self.responses:
            if part in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected url: {url}")

    async def post(self, url: str, **kwargs: Any) -> Any:
        return self._respond(url, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> Any:
        return self._respond(url, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


@pytest.fixture
def http_fake(monkeypatch) -> type[FakeSteamClient]:
    FakeSteamClient.responses = []
    FakeSteamClient.queue = []
    FakeSteamClient.requests = []
    monkeypatch.setattr(steam_mod.httpx, "AsyncClient", lambda *a, **kw: FakeSteamClient())  # noqa: ARG005
    return FakeSteamClient


def summaries_body(*steamids: str) -> FakeResp:
    players = [
        {"steamid": sid, "personaname": f"name-{sid[-3:]}", "profileurl": f"https://steamcommunity.com/profiles/{sid}/"}
        for sid in steamids
    ]
    import json

    return FakeResp(text=json.dumps({"response": {"players": players}}), json_body={"response": {"players": players}})


class TestBuildAuthUrl:
    def test_contains_openid_params(self, monkeypatch):
        monkeypatch.setattr(settings.steam, "return_to_url", "https://bot.example.com/auth/steam/callback")
        url = SteamService().build_auth_url()
        parsed = urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == STEAM_OPENID_ENDPOINT
        q = parse_qs(parsed.query)
        assert q["openid.ns"] == ["http://specs.openid.net/auth/2.0"]
        assert q["openid.mode"] == ["checkid_setup"]
        assert q["openid.identity"] == ["http://specs.openid.net/auth/2.0/identifier_select"]
        assert q["openid.claimed_id"] == ["http://specs.openid.net/auth/2.0/identifier_select"]
        assert q["openid.return_to"] == ["https://bot.example.com/auth/steam/callback"]
        # Realm — только scheme://host, без пути
        assert q["openid.realm"] == ["https://bot.example.com"]

    def test_realm_includes_port(self, monkeypatch):
        monkeypatch.setattr(settings.steam, "return_to_url", "http://localhost:8000/auth/steam/callback")
        q = parse_qs(urlparse(SteamService().build_auth_url()).query)
        assert q["openid.realm"] == ["http://localhost:8000"]


class TestVerifyOpenidResponse:
    @pytest.fixture
    def params(self) -> dict[str, str]:
        return {
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.mode": "id_res",
            "openid.claimed_id": f"https://steamcommunity.com/openid/id/{STEAM_ID}",
            "openid.identity": f"https://steamcommunity.com/openid/id/{STEAM_ID}",
            "openid.return_to": "https://bot.example.com/auth/steam/callback",
        }

    async def test_success(self, http_fake, params):
        http_fake.responses = [("openid/login", FakeResp(text="ns:http://specs.openid.net/auth/2.0\nis_valid:true\n"))]
        assert await SteamService().verify_openid_response(params) == STEAM_ID

    async def test_post_replaces_mode_with_check_authentication(self, http_fake, params):
        http_fake.responses = [("openid/login", FakeResp(text="is_valid:true"))]
        await SteamService().verify_openid_response(params)
        sent = http_fake.requests[0]["data"]
        assert sent["openid.mode"] == "check_authentication"
        # Оригинальные параметры не мутируются
        assert params["openid.mode"] == "id_res"

    async def test_spaced_is_valid_true_normalized(self, http_fake, params):
        http_fake.responses = [("openid/login", FakeResp(text="is_valid: true"))]
        assert await SteamService().verify_openid_response(params) == STEAM_ID

    async def test_invalid_response(self, http_fake, params):
        http_fake.responses = [("openid/login", FakeResp(text="is_valid:false"))]
        assert await SteamService().verify_openid_response(params) is None

    async def test_missing_claimed_id(self, http_fake, params):
        http_fake.responses = [("openid/login", FakeResp(text="is_valid:true"))]
        del params["openid.claimed_id"]
        assert await SteamService().verify_openid_response(params) is None

    @pytest.mark.parametrize(
        "claimed_id",
        [
            "https://steamcommunity.com/openid/id/not-digit",
            "https://steamcommunity.com/openid/id/",
        ],
    )
    async def test_claimed_id_bad_format(self, http_fake, params, claimed_id):
        http_fake.responses = [("openid/login", FakeResp(text="is_valid:true"))]
        params["openid.claimed_id"] = claimed_id
        assert await SteamService().verify_openid_response(params) is None

    @pytest.mark.parametrize(
        "exc",
        [httpx.ConnectError("boom"), httpx.ReadTimeout("t"), httpx.HTTPStatusError("500", request=None, response=None)],
    )  # noqa: E501
    async def test_network_errors_return_none(self, http_fake, params, exc):
        http_fake.responses = [("openid/login", exc)]
        assert await SteamService().verify_openid_response(params) is None

    async def test_raise_for_status_error(self, http_fake, params):
        http_fake.responses = [
            ("openid/login", FakeResp(text="", status_exc=httpx.HTTPStatusError("500", request=None, response=None)))
        ]
        assert await SteamService().verify_openid_response(params) is None


class TestFetchProfileUrl:
    async def test_success(self, http_fake):
        http_fake.responses = [
            (
                "GetPlayerSummaries",
                FakeResp(
                    json_body={"response": {"players": [{"profileurl": "https://steamcommunity.com/id/custom/"}]}}
                ),
            )  # noqa: E501
        ]
        assert await SteamService().fetch_profile_url(STEAM_ID) == "https://steamcommunity.com/id/custom/"
        req = http_fake.requests[0]
        assert req["params"]["steamids"] == STEAM_ID

    async def test_empty_players(self, http_fake):
        http_fake.responses = [("GetPlayerSummaries", FakeResp(json_body={"response": {"players": []}}))]
        assert await SteamService().fetch_profile_url(STEAM_ID) is None

    async def test_profileurl_missing(self, http_fake):
        http_fake.responses = [
            ("GetPlayerSummaries", FakeResp(json_body={"response": {"players": [{"steamid": STEAM_ID}]}}))
        ]  # noqa: E501
        assert await SteamService().fetch_profile_url(STEAM_ID) is None

    async def test_non_json(self, http_fake):
        http_fake.responses = [("GetPlayerSummaries", FakeResp(text="<html>oops</html>"))]
        assert await SteamService().fetch_profile_url(STEAM_ID) is None

    async def test_network_error(self, http_fake):
        http_fake.responses = [("GetPlayerSummaries", httpx.ConnectError("down"))]
        assert await SteamService().fetch_profile_url(STEAM_ID) is None


class TestGetPlayerSummaries:
    async def test_single_chunk(self, http_fake):
        http_fake.responses = [("GetPlayerSummaries", summaries_body(STEAM_ID))]
        result = await SteamService().get_player_summaries([STEAM_ID])
        assert len(result) == 1
        assert isinstance(result[0], SteamPlayerSummary)
        assert result[0].steamid == STEAM_ID
        assert http_fake.requests[0]["params"]["steamids"] == STEAM_ID

    async def test_chunking_250_ids(self, http_fake):
        ids = [str(76561198000000000 + i) for i in range(250)]
        http_fake.queue = [
            summaries_body(*ids[:100]),
            summaries_body(*ids[100:200]),
            summaries_body(*ids[200:]),
        ]
        result = await SteamService().get_player_summaries(ids)
        assert len(http_fake.requests) == 3
        steamids_params = [r["params"]["steamids"] for r in http_fake.requests]
        assert steamids_params[0] == ",".join(ids[:STEAM_SUMMARIES_CHUNK_SIZE])
        assert steamids_params[1] == ",".join(ids[100:200])
        assert steamids_params[2] == ",".join(ids[200:])
        assert len(result) == 250
        assert result[0].steamid == ids[0]
        assert result[-1].steamid == ids[-1]

    async def test_chunk_error_skipped(self, http_fake):
        ids = [str(76561198000000000 + i) for i in range(150)]
        http_fake.queue = [httpx.ConnectError("flaky"), summaries_body(*ids[:100])]
        result = await SteamService().get_player_summaries(ids)
        assert len(http_fake.requests) == 2
        assert len(result) == 100
        assert result[0].steamid == ids[0]

    async def test_non_json_chunk_skipped(self, http_fake):
        http_fake.responses = [("GetPlayerSummaries", FakeResp(text="not json at all"))]
        assert await SteamService().get_player_summaries([STEAM_ID]) == []

    async def test_empty_input(self, http_fake):
        assert await SteamService().get_player_summaries([]) == []
        assert http_fake.requests == []
