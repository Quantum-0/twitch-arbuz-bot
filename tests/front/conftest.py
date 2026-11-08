"""Фикстуры и маркеры фронт-тестов (tests/front).

Ключевая защита — guard внешних вызовов: в браузере перехватывается каждый
запрос, и всё, что идёт не на локальный мини-сервер (twitch, memealerts,
ai-tunnel, любые CDN), наружу не улетает, а роняет тест с именем и URL.

Браузерный слой — АСИНХРОННЫЙ Playwright на общем session-loop pytest-asyncio:
sync-API живёт в собственном event-loop и ломает async-тесты в той же сессии
(поэтому e2e-тесты и запускаются отдельным прогоном).
"""

import base64
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, Route, async_playwright
from pytest_asyncio import is_async_test

from tests.front.server import FrontServer

THIS_DIR = Path(__file__).resolve().parent

# 1x1 прозрачный PNG — заглушка для внешних картинок.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Хосты, которые шаблоны легально подключают как статические/embed-ресурсы.
# Guard молча гасит их пустышками (запрос наружу не улетает). Этот список —
# ОСОЗНАННОЕ решение при добавлении нового CDN/embed в шаблоны.
# ВАЖНО: API-хосты (api/id.twitch.tv, memealerts, ai-tunnel и пр.) сюда
# добавлять НЕЛЬЗЯ — обращение к ним из фронтенда = сломанный фронт, и guard
# обязан ронять тест.
STUBBED_HOSTS = {
    "cdnjs.cloudflare.com",  # Font Awesome (base.html)
    "cdn.jsdelivr.net",  # Chart.js (stats/server)
    "static-cdn.jtvnw.net",  # аватарки/бейджи twitch (картинки)
    "shared.fastly.steamstatic.com",  # кнопка Steam (panel/other.html)
    "mc.yandex.ru",  # Метрика (после cookie-consent)
    "clips.twitch.tv",  # обучающий embed-клип (panel/overlays.html)
    "player.twitch.tv",  # embed-плеер (profile/cmdlist)
    "fonts.googleapis.com",  # шрифты оверлеев
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Тесты папки — маркер front (входит в дефолтный прогон; e2e-фильтр его
    не трогает), увеличенный таймаут (холодный старт Chromium) и session-loop
    (общий с unit-тестами — см. tests/unit/conftest.py).

    Браузерный слой переставляется В КОНЕЦ сессии: у Playwright-транспорта есть
    долгоживущие asyncio-задачи на общем session-loop, и unit-тесты, следящие
    за чистотой loop'а (TestBackgroundTaskNoLeak), должны отработать до него."""
    front_items = [item for item in items if THIS_DIR in item.path.parents]
    other_items = [item for item in items if THIS_DIR not in item.path.parents]
    items[:] = other_items + front_items

    for item in front_items:
        item.add_marker(pytest.mark.front)
        item.add_marker(pytest.mark.timeout(120))
        if is_async_test(item):
            item.add_marker(pytest.mark.asyncio(loop_scope="session"), append=False)


@pytest.fixture(scope="session")
def front_server_url() -> Iterator[str]:
    """Мини-сервер фронтенда: страницы из мок-каталога + /static (+ /api-моки)."""
    server = FrontServer(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.base_url
    finally:
        server.shutdown()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def front_browser() -> AsyncIterator[Browser]:
    try:
        manager = await async_playwright().start()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Playwright недоступен: {exc}")
    try:
        browser = await manager.chromium.launch(headless=True)
    except Exception as exc:
        await manager.stop()
        pytest.skip(
            f"Браузер Playwright не установлен ({exc}). Выполните: poetry run playwright install chromium",
        )
    yield browser
    await browser.close()
    await manager.stop()


def _stub_response(path: str) -> dict:
    """Пустой ответ с корректным MIME, чтобы страница продолжала работу
    без ошибок консоли (внешний ресурс «есть», но он пуст)."""
    ext = Path(path).suffix.lower()
    if ext == ".css":
        return {"status": 200, "content_type": "text/css", "body": ""}
    if ext in (".js", ".mjs"):
        return {"status": 200, "content_type": "application/javascript", "body": ""}
    if ext == ".svg":
        return {"status": 200, "content_type": "image/svg+xml", "body": "<svg xmlns='http://www.w3.org/2000/svg'/>"}
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"):
        return {"status": 200, "content_type": "image/png", "body": PNG_1PX}
    if ext in (".woff", ".woff2", ".ttf"):
        # Шрифты: 404 проходит молча (в отличие от битого бинарника).
        return {"status": 404, "content_type": "application/octet-stream", "body": ""}
    # Без расширения (embed-iframe, прочие GET) — пустой HTML.
    return {"status": 200, "content_type": "text/html; charset=utf-8", "body": ""}


@pytest_asyncio.fixture(loop_scope="session")
async def front_page(
    front_browser: Browser,
    request: pytest.FixtureRequest,
) -> AsyncIterator[Page]:
    """Страница Playwright с двумя глобальными гарантиями:

    1. Ни один запрос не уходит дальше локального сервера: внешние вызовы
       (twitch/memealerts/ai-tunnel/CDN) перехватываются, фиксируются и
       роняют тест в teardown.
    2. Ошибки JS (pageerror + console.error) роняют тест в teardown.
    """
    context = await front_browser.new_context()
    page = await context.new_page()

    external_calls: list[str] = []
    console_errors: list[str] = []

    page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))
    page.on(
        "console",
        lambda msg: console_errors.append(f"console.error: {msg.text}") if msg.type == "error" else None,
    )

    async def guard(route: Route) -> None:
        url = route.request.url
        parsed = urlparse(url)
        if parsed.scheme in ("data", "blob", "about") or parsed.hostname in _LOCAL_HOSTS:
            await route.fallback()
            return
        if parsed.hostname in STUBBED_HOSTS:
            # Легальная статика/embed из шаблонов: гасим молча.
            await route.fulfill(**_stub_response(parsed.path))
            return
        external_calls.append(f"{route.request.method} {url}")
        await route.fulfill(**_stub_response(parsed.path))

    # Guard на КОНТЕКСТЕ, а не странице: накрывает и попапы (window.open),
    # чтобы ни одна вкладка не могла уйти наружу мимо перехвата.
    await context.route("**/*", guard)

    # WebSocket-соединения context.route не перехватывает — отдельный маршрут:
    # локальные проксируем, внешние закрываем и фиксируем (нарушение изоляции).
    async def ws_guard(ws) -> None:  # type: ignore[no-untyped-def]
        parsed = urlparse(ws.url)
        if parsed.scheme in ("ws", "wss") and parsed.hostname not in _LOCAL_HOSTS:
            external_calls.append(f"WS {ws.url}")
            await ws.close()
            return
        await ws.connect_to_server()

    await context.route_web_socket("**/*", ws_guard)  # type: ignore[attr-defined]

    # Для самотеста guard'а (test_guard.py): доступ к списку перехваченных.
    page._front_external_calls = external_calls  # type: ignore[attr-defined]  # noqa: SLF001

    yield page

    await page.close()
    await context.close()

    if external_calls:
        pytest.fail(
            f"Тест «{request.node.name}» попытался сделать реальные ВНЕШНИЕ запросы — "
            "все внешние вызовы (twitch/memealerts/ai-tunnel/CDN) должны мокаться:\n  "
            + "\n  ".join(external_calls)
            + "\nУберите вызов внешнего API из шаблона/JS или замокайте его "
            "(tests/front/api_mocks.py, tests/front/helpers.py:mock_api). Если это легальный "
            "статический/embed-ресурс шаблона — осознанно добавьте хост в STUBBED_HOSTS "
            "в tests/front/conftest.py (API-хосты туда добавлять нельзя).",
            pytrace=False,
        )
    if console_errors:
        pytest.fail(
            f"Тест «{request.node.name}»: ошибки JS-консоли:\n  " + "\n  ".join(console_errors),
            pytrace=False,
        )
