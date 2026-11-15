"""Браузерные тесты страницы профиля: панель «Достижения».

Панель рендерится на сервере; из JS — только стрелки прокрутки ленты
(static/js/profile-achievements.js). Каталог отдаёт профиль с двумя ачивками
(новая одноуровневая + старая уровневая с римской II).
"""

from playwright.async_api import Page

from tests.front.pages import TEMPLATES_ENV, _profile


async def _open(page: Page, server_url: str) -> None:
    await page.goto(server_url.rstrip("/") + "/profile/mock_streamer")
    await page.wait_for_load_state("load")


async def test_panel_renders_achievements(front_page: Page, front_server_url: str) -> None:
    await _open(front_page, front_server_url)

    section = front_page.locator(".profile-achievements")
    await section.wait_for(state="visible")

    # Порядок из шаблона: новые слева (easter_dots получена позже memecoins_sold).
    names = await section.locator(".achievement-name").all_inner_texts()
    assert names == ["Ловец точек", "Мем-магнат"]

    # Римская цифра только у уровневой ачивки.
    romans = await section.locator(".achievement-level").all_inner_texts()
    assert romans == ["II"]

    # Счётчик ачивок в заголовке панели.
    assert (await section.locator(".profile-achievements-count").inner_text()).strip() == "2"


async def test_scroll_arrows_hidden_when_strip_fits(front_page: Page, front_server_url: str) -> None:
    """Две ачивки влезают целиком — стрелок быть не должно."""
    await _open(front_page, front_server_url)

    await front_page.wait_for_selector(".profile-achievements")
    # profile-achievements.js synchronizes arrows on load — wait for JS to settle
    await front_page.wait_for_timeout(200)

    arrows = front_page.locator(".achievements-scroll")
    assert await arrows.count() == 2
    hidden_flags = [await arrow.is_hidden() for arrow in await arrows.all()]
    assert all(hidden_flags)


async def test_no_panel_without_achievements(front_page: Page, front_server_url: str) -> None:  # noqa: ARG001
    """Ни одной ачивки — панели «Достижения» на странице нет."""
    await _open(front_page, front_server_url)

    # Каталог всегда с ачивками; пустой вариант проверяем рендером шаблона
    # с achievements=[] (тег script у панели unconditional — его не трогаем).
    context = _profile(front_server_url)
    context["profile_user"].achievements = []
    html = TEMPLATES_ENV.get_template("profile.html").render(**context)
    assert "profile-achievements-strip" not in html
    assert ">Достижения<" not in html
