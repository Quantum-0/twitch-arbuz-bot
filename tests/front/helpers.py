"""Хелперы браузерных фронт-тестов."""

import json
from typing import NamedTuple
from urllib.parse import urlparse

from playwright.async_api import Page, Route

from tests.front import api_mocks


class APICall(NamedTuple):
    method: str
    path: str
    body: str | None


async def mock_api(page: Page, server_url: str, overrides: dict | None = None) -> list[APICall]:
    """Перехватывает все /api-запросы локального сервера и отвечает моками.

    Возвращает пополняемый список вызовов — на нём тесты проверяют, в какие
    ручки и с какими телами реально ходит фронтенд.
    overrides: {(method, path): (status, body_dict)} — точечные ответы теста.

    Регистрируется ПОСЛЕ guard'а внешних вызовов из conftest (последний
    зарегистрированный route срабатывает первым), поэтому /api полностью
    обслуживается моками и до мини-сервера не доходит.
    """
    calls: list[APICall] = []

    async def handler(route: Route) -> None:
        req = route.request
        path = urlparse(req.url).path
        calls.append(APICall(req.method, path, req.post_data))
        status, body = (overrides or {}).get((req.method, path)) or api_mocks.default_for(req.method, path)
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

    await page.route(f"{server_url.rstrip('/')}/api/**", handler)
    return calls
