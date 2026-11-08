"""Интеграционные браузерные тесты /panel с мок-API.

Реальная страница (рендер из мок-каталога) + реальный static/js; запросы /api
перехватываются моками (helpers.mock_api) с записью — проверяем, что фронт
ходит ровно в те ручки и с теми телами, что ожидает бэк. Ошибки консоли и
внешние вызовы роняет фикстура front_page."""

from collections import Counter

from playwright.async_api import Page

from tests.front.helpers import APICall, mock_api

PANEL_URL_PATH = "/panel"

# Числа из разметки: 24 командных тумблера + мастер + общие чаты + shoutout
# + 4 TG + 6 оверлейных (chat_control ×2, sound, use_twitch_emoji, arbuz, show_names).
EXPECTED_SWITCHES = 37
# star 4 + fireworks 3 + xp-error 3 + pair 6 + chibi 1 + slovotron 3 + ai-sticker 2.
EXPECTED_SLIDERS = 22
# memealerts ×2 + telegram + heat + sse.
EXPECTED_STATUS_CARDS = 5


async def _open_panel(page: Page, server_url: str, overrides: dict | None = None) -> list[APICall]:
    calls = await mock_api(page, server_url, overrides)
    await page.goto(server_url.rstrip("/") + PANEL_URL_PATH)
    await page.wait_for_function("!!customElements.get('q0ui-switch')", timeout=15_000)
    await page.wait_for_timeout(400)  # первый опрос статус-карточек и инициализация секций
    return calls


async def test_panel_components_upgraded(front_page: Page, front_server_url: str) -> None:
    await _open_panel(front_page, front_server_url)
    page = front_page
    assert await page.locator("q0ui-switch").count() == EXPECTED_SWITCHES
    assert await page.locator("q0ui-slider").count() == EXPECTED_SLIDERS
    assert await page.locator("q0ui-status-card").count() == EXPECTED_STATUS_CARDS
    assert await page.locator("q0ui-number#coin-count").count() == 1


async def test_master_toggle_saves_setting(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open_panel(page, front_server_url)

    master = page.locator('q0ui-switch[name="enable_chat_bot"]')
    assert await master.get_attribute("aria-checked") == "true"  # из мок-контекста включён
    await master.click()  # выключаем
    await master.click()  # и снова включаем

    updates = [c for c in calls if c.path == "/api/user/update_settings"]
    assert [(c.method, c.body) for c in updates] == [
        ("POST", "enable_chat_bot=false"),
        ("POST", "enable_chat_bot=true"),
    ]
    # После включения зависимая группа раскрыта.
    assert "active" in (await page.locator("#dependent-toggles").get_attribute("class") or "")


async def test_overlay_slider_and_secret_in_link(front_page: Page, front_server_url: str) -> None:
    page = front_page
    await _open_panel(page, front_server_url)

    star_card = page.locator('.overlay-card[data-base$="overlay/star"]')
    link = star_card.locator(".overlay-link")
    link_text = await link.text_content()
    assert "pos=0.5" in (link_text or "")
    assert "secret=mock-secret" in (link_text or "")  # chat_control включён из разметки

    # Настройки карточки свёрнуты — раскрываем, чтобы слайдер стал видимым.
    await star_card.locator(".overlay-toggle-settings").click()

    # Клавиатура: End = максимум → ссылка обновилась.
    await star_card.locator('q0ui-slider[data-param="pos"]').press("End")
    assert "pos=1" in (await link.text_content() or "")

    # Выключили управление из чата — секрет из ссылки исчезает.
    await star_card.locator('q0ui-switch[data-param="chat_control"]').click()
    text = await link.text_content()
    assert "chat_control=false" in (text or "")
    assert "secret" not in (text or "")


async def test_reward_button_flow(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open_panel(page, front_server_url)

    # Мок memealerts-reward: result=false, state=missing → «Создать награду».
    btn = page.locator("#memealerts-reward-btn")
    await page.wait_for_function(
        "document.getElementById('memealerts-reward-btn').dataset.action === 'create'",
        timeout=10_000,
    )
    assert await btn.text_content() == "Создать награду"

    await btn.click()
    await page.wait_for_timeout(200)
    rewards = [c for c in calls if c.path == "/api/user/memealerts/reward"]
    assert [c.method for c in rewards] == ["PUT"]


async def test_coin_stepper_debounce(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open_panel(page, front_server_url)

    await page.evaluate(
        """() => {
            const el = document.getElementById('coin-count');
            el.shadowRoot.querySelector('.btn-plus').click();
            el.shadowRoot.querySelector('.btn-plus').click();
        }""",
    )
    assert await page.evaluate("document.getElementById('coin-count').value") == 12

    await page.wait_for_timeout(3_500)  # debounce 3с
    coins = [c for c in calls if c.path == "/api/user/memealerts/coins"]
    assert len(coins) == 1, "после debounce должен уйти ровно один запрос"
    assert '"count":12' in (coins[0].body or "")

    # Живой ввод без коммита (Enter/блюр не нажаты): debounce обязан взять
    # значение из события, а не застарелый coins.value (регрессия).
    await page.evaluate(
        """() => {
            const el = document.getElementById('coin-count');
            const field = el.shadowRoot.querySelector('.field');
            field.value = '55';
            field.dispatchEvent(new Event('input', { bubbles: true }));
        }""",
    )
    await page.wait_for_timeout(3_500)
    coins = [c for c in calls if c.path == "/api/user/memealerts/coins"]
    assert len(coins) == 2
    assert '"count":55' in (coins[1].body or "")


async def test_telegram_section_initializes(front_page: Page, front_server_url: str) -> None:
    page = front_page
    await _open_panel(page, front_server_url)

    await page.wait_for_function(
        """() => {
            const main = document.getElementById('telegram-main');
            const toggle = document.getElementById('tg-stream-notif-toggle');
            return main && !main.hidden && toggle && toggle.checked === true;
        }""",
        timeout=10_000,
    )
    # Название чата из мока отображается.
    assert "Мок-канал" in (await page.locator("#tg-stream-chat").text_content() or "")


async def test_status_cards_poll_right_endpoints(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open_panel(page, front_server_url)
    polled = {c.path for c in calls if c.path.startswith("/api/user/check/")}
    assert {
        "/api/user/check/memealerts-token",
        "/api/user/check/memealerts-reward",
        "/api/user/check/telegram",
        "/api/user/check/heat-installed",
        "/api/user/check/sse",
    } <= polled
    # Ровно один начальный опрос на карточку (апгрейд не должен плодить
    # интервалы — регрессия на двойной поллинг).
    counts = Counter((c.method, c.path) for c in calls if c.path.startswith("/api/user/check/"))
    for endpoint in polled:
        assert counts[("GET", endpoint)] == 1, f"{endpoint}: {counts[('GET', endpoint)]} начальных опросов"


async def test_telegram_toggle_hits_only_telegram_api(front_page: Page, front_server_url: str) -> None:
    """Регрессия: TG-тумблеры раньше двойным огнём били и в update_settings
    (лишний тост + полный ре-синк чат-бота на каждый клик)."""
    page = front_page
    calls = await _open_panel(page, front_server_url)

    await page.locator("#tg-stream-notif-toggle").click()
    await page.wait_for_timeout(300)

    tg_posts = [c for c in calls if c.path == "/api/user/telegram/settings" and c.method == "POST"]
    assert len(tg_posts) == 1, "клик по TG-тумблеру должен давать ровно один POST"
    assert [c for c in calls if c.path == "/api/user/update_settings"] == [], (
        "TG-тумблер не должен дёргать /api/user/update_settings"
    )


async def test_action_buttons_hit_right_endpoints(front_page: Page, front_server_url: str) -> None:
    """Все кнопки-действия панели привязаны (бывшие inline onclick убраны):
    клик обязан уйти в правильную ручку правильным методом и обновить UI."""
    page = front_page
    # Heat «не установлен» — иначе initHeat прячет .plugin-required с кнопкой.
    overrides = {
        ("GET", "/api/user/check/heat-installed"): (200, {"result": False, "problems": [], "warnings": []}),
    }
    calls = await _open_panel(page, front_server_url, overrides)

    def sent(method: str, path: str) -> bool:
        return (method, path) in [(c.method, c.path) for c in calls]

    # Сброс ключа оверлеев (быв. onclick="resetOverlaySecret()").
    await page.locator("#reset-overlay-secret-btn").click()
    await page.wait_for_timeout(150)
    assert sent("POST", "/api/user/overlay/reset-secret")

    # Установка Heat — кнопки внутри .plugin-required (быв. onclick="setupHeat()").
    await page.locator(".plugin-required .install-btn").nth(0).click()
    await page.wait_for_timeout(150)
    assert sent("GET", "/api/user/install-heat")

    # Отвязка Telegram: запрос + кнопки меняются местами (быв. onclick).
    await page.locator("#tg-unlink-btn").click()
    await page.wait_for_timeout(150)
    assert sent("POST", "/api/user/telegram/unlink-account")
    assert await page.locator("#tg-unlink-btn").is_hidden()
    assert await page.locator("#tg-link-btn").is_visible()

    # Отвязка Steam: запрос + кнопка «Привязать» (ссылка) проявляется.
    await page.locator("#steam-unlink-btn").click()
    await page.wait_for_timeout(150)
    assert sent("POST", "/api/user/steam/unlink")
    assert await page.locator("#steam-unlink-btn").is_hidden()
    assert await page.locator("#steam-link").is_visible()

    # Отключение интеграции Memealerts: DELETE + блок заменяется кнопкой подключения
    # (быв. onclick="disconnectMemealerts()").
    await page.locator("#disconnect-memealerts-btn").click()
    await page.wait_for_timeout(150)
    assert sent("DELETE", "/api/user/memealerts")
    assert await page.locator("#memealerts-v2-connected").is_hidden()
    assert await page.locator("#memealerts-v2-disconnected").is_visible()

    # «Сохранить шаблон» (быв. onclick="telegramSaveSettings()").
    await page.locator("#tg-save-templates-btn").click()
    await page.wait_for_timeout(150)
    assert sent("POST", "/api/user/telegram/settings")

    # «Да кто такой этот ваш Heat?!» — раскрытие клипа.
    await page.locator("#wtf-is-heat-link").click()
    assert await page.locator("#wtf-is-heat-clip").is_visible()


async def test_telegram_chat_buttons(front_page: Page, front_server_url: str) -> None:
    """Кнопки подключения/отключения TG-чатов (data-tg-connect/-disconnect)."""
    page = front_page
    page.on("dialog", lambda dialog: dialog.accept())  # confirm() при отключении
    overrides = {
        ("POST", "/api/user/telegram/connect"): (200, {"title": "Мок", "message": "Ссылка создана"}),
    }
    calls = await _open_panel(page, front_server_url, overrides)

    # Мок: stream-чат подключён → кнопка «Отключить» видна, «Добавить в канал» скрыта.
    assert await page.locator("#tg-stream-disconnect-btn").is_visible()
    assert await page.locator("#tg-stream-connect-btns").is_hidden()

    # Отключение чата (confirm авто-принят) → POST disconnect/<scope> + перезагрузка настроек.
    await page.locator("#tg-stream-disconnect-btn").click()
    await page.wait_for_timeout(300)
    assert ("POST", "/api/user/telegram/disconnect/stream") in [(c.method, c.path) for c in calls]

    # Подключение: делаем чат «неподключённым» и жмём «Добавить в канал».
    await page.evaluate(
        """() => {
            document.getElementById('tg-stream-connect-btns').hidden = false;
        }""",
    )
    await page.locator("#tg-stream-connect-btns button").nth(0).click()
    await page.wait_for_timeout(200)
    tg_connects = [c for c in calls if c.path == "/api/user/telegram/connect"]
    assert len(tg_connects) == 1
    assert '"scope":"stream"' in (tg_connects[0].body or "")
    assert '"chat_type":"channel"' in (tg_connects[0].body or "")


async def test_main_page_smoke(front_page: Page, front_server_url: str) -> None:
    await front_page.goto(front_server_url)
    await front_page.wait_for_selector("#twitchLoginBtn")


async def test_ui_demo_smoke(front_page: Page, front_server_url: str) -> None:
    await front_page.goto(front_server_url + "ui-demo")
    await front_page.wait_for_function("!!customElements.get('q0ui-switch')", timeout=15_000)
    assert await front_page.locator("q0ui-switch").count() >= 3
