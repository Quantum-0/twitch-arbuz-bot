import os
from pathlib import Path

import pytest
from playwright._impl._api_structures import SetCookieParam
from playwright.sync_api import Browser, BrowserContext, sync_playwright


def pytest_collection_modifyitems(items):
    """Все e2e-тесты помечаются маркером `e2e` и исключаются из дефолтного прогона
    (addopts `-m "not e2e"`): им нужны прод-окружение и секреты TEST_*."""
    this_dir = Path(__file__).parent
    for item in items:
        if this_dir in item.path.parents:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session", autouse=True)
def cookie_session_value() -> str:
    return os.environ["TEST_COOKIE_SESSION_VALUE"]


@pytest.fixture(scope="session")
def twitch_creds() -> tuple[str, str]:
    return os.environ["TEST_TWITCH_USERNAME"], os.environ["TEST_TWITCH_PASSWORD"]


@pytest.fixture(scope="session")
def cookie_session(cookie_session_value, domain) -> SetCookieParam:
    return SetCookieParam(
        name="session",
        value=cookie_session_value,
        domain=domain,
        path="/",
        secure=True,
    )


@pytest.fixture(scope="session")
def domain() -> str:
    return "bot.quantum0.ru"


@pytest.fixture(scope="session")
def main_page_url(domain) -> str:
    return f"https://{domain}/"


@pytest.fixture(scope="session")
def browser() -> Browser:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def context_unauthorized(browser) -> BrowserContext:
    ctx = browser.new_context()
    yield ctx
    ctx.close()


@pytest.fixture(scope="session")
def context(browser, cookie_session) -> BrowserContext:
    ctx = browser.new_context()
    ctx.add_cookies([cookie_session])
    yield ctx
    ctx.close()
