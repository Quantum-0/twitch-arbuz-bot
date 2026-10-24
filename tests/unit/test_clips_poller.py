"""Юнит-тесты ClipsPollerService: яркие моменты (дедуп, формат отправки, парсинг дат)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from services.clips_poller import ClipsPollerService


class FakeMQTT:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.published.append((topic, payload))


class FakeSession:
    """Минимальная эмуляция AsyncSession для _update_last_highlight_date."""

    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.committed = False

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def execute(self, stmt: Any) -> None:
        self.statements.append(stmt)

    async def commit(self) -> None:
        self.committed = True


def build_service(mqtt: FakeMQTT, session: FakeSession) -> ClipsPollerService:
    service = object.__new__(ClipsPollerService)
    service._db_session_factory = lambda: session
    service._token_service = None
    service._mqtt = mqtt
    return service


def build_user() -> SimpleNamespace:
    return SimpleNamespace(id=7, twitch_id="12345", login_name="streamer")


def build_tg(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "clips_chat_id": "-100500",
        "clips_delivery": "link",
        "clips_mode": "all",
        "last_clip_date": None,
        "last_highlight_date": datetime(2026, 10, 20, 12, 0, 0),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def highlight(video_id: str, created_at: str, title: str = "Момент") -> dict[str, Any]:
    return {"id": video_id, "title": title, "url": f"https://www.twitch.tv/videos/{video_id}", "created_at": created_at}


# Get Videos отдаёт created_at с наносекундной точностью.
TS_NEW_2 = "2026-10-20T18:00:00.123456789+00:00"
TS_NEW_1 = "2026-10-20T16:30:00.987654321+00:00"
TS_OLD = "2026-10-19T10:00:00.000000001+00:00"


@pytest.mark.asyncio
async def test_highlights_sent_oldest_first_and_cursor_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Свежие моменты отправляются ссылкой от старых к новым, курсор — max(created_at)."""
    mqtt = FakeMQTT()
    session = FakeSession()
    service = build_service(mqtt, session)
    # _fetch_highlights отдаёт свежими первыми (как Twitch API).
    fetched = [highlight("222", TS_NEW_2), highlight("111", TS_NEW_1)]
    monkeypatch.setattr(service, "_fetch_highlights", lambda **_kwargs: _async_value(fetched))

    await service._poll_user_highlights("token", build_user(), build_tg())

    assert [t for t, _ in mqtt.published] == ["telegram/send_message", "telegram/send_message"]
    texts = [p["message_text"] for _, p in mqtt.published]
    assert texts == [
        "⭐ Момент\n🔗 https://www.twitch.tv/videos/111",
        "⭐ Момент\n🔗 https://www.twitch.tv/videos/222",
    ]
    # send_message payload содержит request_id и chat_id clips-чата.
    assert all(p["chat_id"] == "-100500" and p["request_id"] for _, p in mqtt.published)

    assert session.committed
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    assert compiled.params["last_highlight_date"] == datetime(2026, 10, 20, 18, 0, 0, 123456)


@pytest.mark.asyncio
async def test_highlights_first_run_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Первый запуск (last_highlight_date=None): threshold = NOW-1s, ничего не шлём и не пишем."""
    mqtt = FakeMQTT()
    session = FakeSession()
    service = build_service(mqtt, session)
    calls: list[dict[str, Any]] = []

    async def fake_fetch(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(service, "_fetch_highlights", fake_fetch)
    before = datetime.now(UTC).replace(tzinfo=None)

    await service._poll_user_highlights("token", build_user(), build_tg(last_highlight_date=None))

    assert "threshold" in calls[0]
    assert before - timedelta(seconds=5) <= calls[0]["threshold"] <= datetime.now(UTC).replace(tzinfo=None)
    assert mqtt.published == []
    assert not session.committed


@pytest.mark.asyncio
async def test_highlight_without_url_builds_fallback_link() -> None:
    mqtt = FakeMQTT()
    service = build_service(mqtt, FakeSession())

    await service._send_highlight("-100500", {"id": "42", "title": "Без url", "created_at": TS_NEW_1})

    assert mqtt.published[0][1]["message_text"] == "⭐ Без url\n🔗 https://www.twitch.tv/videos/42"


class FakeResponse:
    def __init__(self, page: dict[str, Any]) -> None:
        self._page = page
        self.is_success = True
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._page


class FakeVideosClient:
    """httpx-заглушка: отдаёт страницы Get Videos по порядку, помнит запросы."""

    responses: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def __aenter__(self) -> "FakeVideosClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def get(self, url: str, params: dict[str, str] | None = None, **_kwargs: Any) -> FakeResponse:
        self.requests.append({"url": url, "params": dict(params or {})})
        page = type(self).responses.pop(0)
        return FakeResponse(page)


async def _async_value(value: Any) -> Any:
    return value


@pytest.mark.asyncio
async def test_fetch_highlights_stops_on_older_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пагинация прекращается на первом моменте старее threshold."""
    threshold = datetime(2026, 10, 20, 12, 0, 0)
    client_holder: dict[str, FakeVideosClient] = {}

    def make_client() -> FakeVideosClient:
        client_holder["client"] = FakeVideosClient()
        return client_holder["client"]

    FakeVideosClient.responses = [
        {
            "data": [
                highlight("222", TS_NEW_2),
                highlight("111", TS_NEW_1),
                highlight("000", TS_OLD),  # старее threshold — стоп, не запрашиваем следующую страницу
            ],
            "pagination": {"cursor": "next"},
        },
        # Вторая страница не должна запрашиваться; если запросят — тест упадёт по пустому списку.
    ]
    monkeypatch.setattr("services.clips_poller.httpx.AsyncClient", make_client)
    service = build_service(FakeMQTT(), FakeSession())

    result = await service._fetch_highlights(access_token="token", user_id="12345", threshold=threshold)

    assert [v["id"] for v in result] == ["222", "111"]
    client = client_holder["client"]
    assert len(client.requests) == 1
    req = client.requests[0]
    assert req["url"] == "https://api.twitch.tv/helix/videos"
    assert req["params"]["user_id"] == "12345"
    assert req["params"]["type"] == "highlight"


def test_parse_created_at_formats() -> None:
    """Клиповый формат (Z, секунды) и видео-формат (наносекунды) парсятся одинаково."""
    assert service_parse("2026-09-16T02:45:30Z") == datetime(2026, 9, 16, 2, 45, 30)
    # 9 цифр дробной части усекаются до 6.
    assert service_parse(TS_NEW_1) == datetime(2026, 10, 20, 16, 30, 0, 987654)
    # Меньше 7 цифр дробной части — fromisoformat понимает и так.
    assert service_parse("2026-10-20T16:30:00.5Z") == datetime(2026, 10, 20, 16, 30, 0, 500000)


def service_parse(value: str) -> datetime:
    service = object.__new__(ClipsPollerService)
    return service._parse_created_at(value)
