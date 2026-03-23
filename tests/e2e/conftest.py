import os

from playwright._impl._api_structures import SetCookieParam
from playwright.sync_api import BrowserContext, sync_playwright, Browser
from pytest import fixture


@fixture(scope="session", autouse=True)
def cookie_session_value() -> str:
    return os.environ['TEST_COOKIE_SESSION_VALUE']

@fixture(scope="session")
def twitch_creds() -> tuple[str, str]:
    return os.environ['TEST_TWITCH_USERNAME'], os.environ['TEST_TWITCH_PASSWORD']

@fixture(scope="session")
def cookie_session(cookie_session_value, domain) -> SetCookieParam:
    return SetCookieParam(
        name="session",
        value=cookie_session_value,
        domain=domain,
        path="/",
        secure=True,
    )

@fixture(scope="session")
def domain() -> str:
    return "bot.quantum0.ru"

@fixture(scope="session")
def main_page_url(domain) -> str:
    return f"https://{domain}/"

@fixture(scope="session")
def browser() -> Browser:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        yield browser
        browser.close()

@fixture(scope="session")
def context_unauthorized(browser) -> BrowserContext:
    ctx = browser.new_context()
    yield ctx
    ctx.close()

@fixture(scope="session")
def context(browser, cookie_session) -> BrowserContext:
    ctx = browser.new_context()
    ctx.add_cookies([cookie_session])
    yield ctx
    ctx.close()
