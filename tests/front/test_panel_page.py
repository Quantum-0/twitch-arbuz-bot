"""Интеграционные браузерные тесты страниц панели (/panel + /panel/<section>).

Реальные страницы (рендер из мок-каталога) + реальный static/js; запросы /api
перехватываются моками (helpers.mock_api) с записью — проверяем, что фронт
ходит ровно в те ручки и с теми телами, что ожидает бэк. Ошибки консоли и
внешние вызовы роняет фикстура front_page."""

from collections import Counter

from playwright.async_api import Page

from tests.front.helpers import APICall, mock_api

# Числа из разметки chatbot-страницы: 24 командных тумблера + мастер + общие чаты.
CHATBOT_SWITCHES = 26
# star 4 + fireworks 3 + xp-error 3 + pair 6 + chibi 1 + slovotron 3 + ai-sticker 2.
OVERLAY_SLIDERS = 22
# chat_control ×2, sound, use_twitch_emoji, arbuz, show_names.
OVERLAY_SWITCHES = 6


async def _open(
    page: Page,
    server_url: str,
    path: str = "/panel",
    overrides: dict | None = None,
) -> list[APICall]:
    """Открывает страницу панели и ждёт апгрейда q0ui-компонентов.

    Хаб (/panel) компонентов не содержит — условие сразу истинно."""
    calls = await mock_api(page, server_url, overrides)
    await page.goto(server_url.rstrip("/") + path)
    await page.wait_for_function(
        "!!customElements.get('q0ui-switch') || !document.querySelector('q0ui-switch')",
        timeout=15_000,
    )
    await page.wait_for_timeout(400)  # первый опрос статус-карточек и инициализация секций
    return calls


# ── Хаб /panel ───────────────────────────────────────────────────────────────


async def test_hub_cards_and_links(front_page: Page, front_server_url: str) -> None:
    await _open(front_page, front_server_url)
    page = front_page

    cards = page.locator(".panel-hub-card")
    assert await cards.count() == 9

    hrefs = {await card.get_attribute("href") for card in await cards.all()}
    assert {
        "/panel/chatbot",
        "/panel/memealerts",
        "/panel/telegram",
        "/panel/ai-stickers",
        "/panel/tts",
        "/panel/overlays",
        "/panel/other",
        "https://resizer.quantum0.ru",
        "https://cut.quantum0.ru",
    } <= hrefs

    # Внешние инструменты открываются в новой вкладке.
    external = page.locator(".panel-hub-card--external")
    assert await external.count() == 2
    for card in await external.all():
        assert await card.get_attribute("target") == "_blank"
        assert await card.get_attribute("rel") == "noopener"

    # Бейджи состояния интеграций (из мок-контекста всё подключено).
    badges = {(await b.text_content() or "").strip() for b in await page.locator(".panel-hub-badge").all()}
    assert {"Включён", "Подключено"} <= badges

    # TTS помечен как разрабатываемый функционал.
    tts_card = page.locator('.panel-hub-card[href="/panel/tts"]')
    assert "В РАЗРАБОТКЕ" in (await tts_card.locator(".panel-hub-dev-badge").text_content() or "")


async def test_hub_makes_no_api_calls(front_page: Page, front_server_url: str) -> None:
    """Хаб — статичная навигация: ни одного запроса к API."""
    calls = await _open(front_page, front_server_url)
    assert [c for c in calls if c.path.startswith("/api/")] == []


# ── /panel/chatbot ──────────────────────────────────────────────────────────


async def test_chatbot_categories_structure(front_page: Page, front_server_url: str) -> None:
    await _open(front_page, front_server_url, "/panel/chatbot")
    page = front_page

    categories = page.locator(".command-category")
    assert await categories.count() == 4

    assert await page.locator("q0ui-switch").count() == CHATBOT_SWITCHES

    # У каждой категории — кнопка «Включить все» и справка (свёрнутая по умолчанию).
    assert await page.locator(".toggle-all-btn").count() == 4
    helps = page.locator("details.command-help")
    assert await helps.count() == 4
    for help_details in await helps.all():
        assert await help_details.get_attribute("open") is None

    # Справка раскрывается и содержит примеры целей target-команд.
    first_help = helps.nth(0)
    await first_help.locator("summary").click()
    assert await first_help.get_attribute("open") is not None
    body = await first_help.locator(".command-help-body").text_content()
    assert "!кусь @Ник" in (body or "")


async def test_chatbot_help_contains_section_links(front_page: Page, front_server_url: str) -> None:
    """Справка ссылок ведёт в разделы панели: «Другие настройки» и интеграция Memealerts."""
    await _open(front_page, front_server_url, "/panel/chatbot")
    content = await front_page.content()
    assert 'href="/panel/other"' in content
    assert 'href="/panel/memealerts"' in content


async def test_chatbot_rapid_toggles_save_in_order(front_page: Page, front_server_url: str) -> None:
    """Быстрые переключения одного тумблера сохраняются строго по порядку
    кликов (раньше параллельные запросы могли примениться наоборот)."""
    page = front_page
    calls = await _open(page, front_server_url, "/panel/chatbot")

    master = page.locator('q0ui-switch[name="enable_chat_bot"]')
    await master.click()  # выключаем
    await master.click()  # тут же включаем обратно

    await page.wait_for_timeout(400)
    updates = [c for c in calls if c.path == "/api/user/update_settings"]
    assert [c.body for c in updates] == ["enable_chat_bot=false", "enable_chat_bot=true"]


async def test_chatbot_master_toggle_saves_setting(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open(page, front_server_url, "/panel/chatbot")

    master = page.locator('q0ui-switch[name="enable_chat_bot"]')
    assert await master.get_attribute("aria-checked") == "true"  # из мок-контекста включён
    await master.click()  # выключаем
    await master.click()  # и снова включаем

    updates = [c for c in calls if c.path == "/api/user/update_settings"]
    assert [(c.method, c.body) for c in updates] == [
        ("POST", "enable_chat_bot=false"),
        ("POST", "enable_chat_bot=true"),
    ]
    # После включения зависимые категории раскрыты.
    assert "active" in (await page.locator("#dependent-toggles").get_attribute("class") or "")


async def test_chatbot_bulk_enable_all(front_page: Page, front_server_url: str) -> None:
    """«Включить все» в категории чекает только выключенные команды и
    сохраняет каждую отдельным POST'ом."""
    page = front_page
    calls = await _open(page, front_server_url, "/panel/chatbot")

    # Выключаем три команды категории «Взаимодействие» обычными кликами.
    for name in ("enable_bite", "enable_hug", "enable_feed"):
        await page.locator(f'q0ui-switch[name="{name}"]').click()
    await page.wait_for_timeout(200)
    calls.clear()

    await page.locator("#cmd-category-interactions .toggle-all-btn").click()
    await page.wait_for_timeout(300)

    updates = [c for c in calls if c.path == "/api/user/update_settings"]
    assert sorted(c.body for c in updates) == [
        "enable_bite=true",
        "enable_feed=true",
        "enable_hug=true",
    ]
    # Все тумблеры категории теперь включены.
    for name in ("enable_bite", "enable_hug", "enable_feed"):
        assert await page.locator(f'q0ui-switch[name="{name}"]').get_attribute("aria-checked") == "true"


# ── /panel/memealerts ───────────────────────────────────────────────────────


async def test_memealerts_old_integration_removed(front_page: Page, front_server_url: str) -> None:
    """Старая ручная интеграция больше не отображается."""
    await _open(front_page, front_server_url, "/panel/memealerts")
    content = await front_page.content()
    assert "Ручная интеграция" not in content
    assert "больше не поддерживается" not in content


async def test_memealerts_reward_button_flow(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open(page, front_server_url, "/panel/memealerts")

    # Мок memealerts-reward: result=false, state=missing → «Создать награду».
    btn = page.locator("#memealerts-reward-btn")
    await page.wait_for_function(
        "document.getElementById('memealerts-reward-btn').dataset.action === 'create'",
        timeout=10_000,
    )
    assert await btn.text_content() == "Создать награду"

    await btn.click()
    await page.wait_for_timeout(200)
    rewards = [c for c in calls if c.path == "/api/user/memealerts/reward" and c.method in ("PUT", "PATCH")]
    assert [c.method for c in rewards] == ["PUT"]


async def test_memealerts_coin_stepper_debounce(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open(page, front_server_url, "/panel/memealerts")

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


async def test_memealerts_disconnect_button(front_page: Page, front_server_url: str) -> None:
    """Отключение интеграции: DELETE + блок заменяется кнопкой подключения."""
    page = front_page
    calls = await _open(page, front_server_url, "/panel/memealerts")

    await page.locator("#disconnect-memealerts-btn").click()
    await page.wait_for_timeout(200)
    assert ("DELETE", "/api/user/memealerts") in [(c.method, c.path) for c in calls]
    assert await page.locator("#memealerts-v2-connected").is_hidden()
    assert await page.locator("#memealerts-v2-disconnected").is_visible()


async def test_memealerts_reward_cost_control(front_page: Page, front_server_url: str) -> None:
    """Стоимость награды подтягивается из API при загрузке и сохраняется PATCH'ем."""
    page = front_page
    calls = await _open(page, front_server_url, "/panel/memealerts")

    # Стоимость приходит из мока (5000) — поле разблокируется и заполняется.
    await page.wait_for_function(
        """() => {
            const el = document.getElementById('reward-cost');
            return el && !el.hasAttribute('disabled') && Number(el.value) === 5000;
        }""",
        timeout=10_000,
    )

    await page.locator("#reward-cost-save-btn").click()
    await page.wait_for_timeout(200)

    patches = [c for c in calls if c.path == "/api/user/memealerts/reward" and c.method == "PATCH"]
    assert len(patches) == 1
    assert '"cost":5000' in (patches[0].body or "")


async def test_memealerts_status_cards_poll_right_endpoints(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open(page, front_server_url, "/panel/memealerts")
    polled = {c.path for c in calls if c.path.startswith("/api/user/check/")}
    assert polled == {
        "/api/user/check/memealerts-token",
        "/api/user/check/memealerts-reward",
    }
    # Ровно один начальный опрос на карточку (апгрейд не должен плодить
    # интервалы — регрессия на двойной поллинг).
    counts = Counter((c.method, c.path) for c in calls if c.path.startswith("/api/user/check/"))
    for endpoint in polled:
        assert counts[("GET", endpoint)] == 1, f"{endpoint}: {counts[('GET', endpoint)]} начальных опросов"


# ── /panel/telegram ─────────────────────────────────────────────────────────


async def test_telegram_section_initializes(front_page: Page, front_server_url: str) -> None:
    page = front_page
    await _open(page, front_server_url, "/panel/telegram")

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


async def test_telegram_toggle_hits_only_telegram_api(front_page: Page, front_server_url: str) -> None:
    """Регрессия: TG-тумблеры раньше двойным огнём били и в update_settings
    (лишний тост + полный ре-синк чат-бота на каждый клик)."""
    page = front_page
    calls = await _open(page, front_server_url, "/panel/telegram")

    await page.locator("#tg-stream-notif-toggle").click()
    await page.wait_for_timeout(300)

    tg_posts = [c for c in calls if c.path == "/api/user/telegram/settings" and c.method == "POST"]
    assert len(tg_posts) == 1, "клик по TG-тумблеру должен давать ровно один POST"
    assert [c for c in calls if c.path == "/api/user/update_settings"] == [], (
        "TG-тумблер не должен дёргать /api/user/update_settings"
    )


async def test_telegram_chat_buttons(front_page: Page, front_server_url: str) -> None:
    """Кнопки подключения/отключения TG-чатов (data-tg-connect/-disconnect)."""
    page = front_page
    page.on("dialog", lambda dialog: dialog.accept())  # confirm() при отключении
    overrides = {
        ("POST", "/api/user/telegram/connect"): (200, {"title": "Мок", "message": "Ссылка создана"}),
    }
    calls = await _open(page, front_server_url, "/panel/telegram", overrides)

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


async def test_telegram_account_link_buttons(front_page: Page, front_server_url: str) -> None:
    """Личная привязка TG-аккаунта переехала в раздел Telegram:
    мок-юзер привязан → видна «Отвязать», клик уходит в unlink-account."""
    page = front_page
    calls = await _open(page, front_server_url, "/panel/telegram")

    assert await page.locator("#tg-unlink-btn").is_visible()
    assert await page.locator("#tg-link-btn").is_hidden()
    assert "mock_tg" in (await page.locator("#tg-username-line").text_content() or "")

    await page.locator("#tg-unlink-btn").click()
    await page.wait_for_timeout(150)
    assert ("POST", "/api/user/telegram/unlink-account") in [(c.method, c.path) for c in calls]
    assert await page.locator("#tg-unlink-btn").is_hidden()
    assert await page.locator("#tg-link-btn").is_visible()


async def test_telegram_save_templates(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open(page, front_server_url, "/panel/telegram")

    await page.locator("#tg-save-templates-btn").click()
    await page.wait_for_timeout(150)
    assert ("POST", "/api/user/telegram/settings") in [(c.method, c.path) for c in calls]


async def test_telegram_status_card_polls_once(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open(page, front_server_url, "/panel/telegram")
    polled = {c.path for c in calls if c.path.startswith("/api/user/check/")}
    assert polled == {"/api/user/check/telegram"}
    assert len([c for c in calls if c.path == "/api/user/check/telegram"]) == 1


# ── /panel/overlays ─────────────────────────────────────────────────────────


async def test_overlay_slider_and_secret_in_link(front_page: Page, front_server_url: str) -> None:
    page = front_page
    await _open(page, front_server_url, "/panel/overlays")

    assert await page.locator("q0ui-slider").count() == OVERLAY_SLIDERS
    assert await page.locator("q0ui-switch").count() == OVERLAY_SWITCHES

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


async def test_overlay_settings_persist_across_reload(front_page: Page, front_server_url: str) -> None:
    """Настройки оверлеев запоминаются локально: после перезагрузки страницы
    значения не сбрасываются к дефолтам разметки."""
    page = front_page
    await _open(page, front_server_url, "/panel/overlays")

    star_card = page.locator('.overlay-card[data-base$="overlay/star"]')
    link = star_card.locator(".overlay-link")
    await star_card.locator(".overlay-toggle-settings").click()
    await star_card.locator('q0ui-slider[data-param="pos"]').press("End")
    assert "pos=1" in (await link.text_content() or "")

    # Перезагрузка: слайдер восстановлен из локального хранилища, не из разметки.
    await page.reload()
    await page.wait_for_function("!!customElements.get('q0ui-switch')", timeout=15_000)
    await page.wait_for_timeout(400)

    star_card = page.locator('.overlay-card[data-base$="overlay/star"]')
    link_text = await star_card.locator(".overlay-link").text_content()
    assert "pos=1" in (link_text or "")
    assert "pos=0.5" not in (link_text or "")


async def test_overlays_status_cards_and_heat(front_page: Page, front_server_url: str) -> None:
    page = front_page
    # Heat «не установлен» — иначе initHeat прячет .plugin-required с кнопкой.
    overrides = {
        ("GET", "/api/user/check/heat-installed"): (200, {"result": False, "problems": [], "warnings": []}),
    }
    calls = await _open(page, front_server_url, "/panel/overlays", overrides)

    polled = {c.path for c in calls if c.path.startswith("/api/user/check/")}
    assert polled == {"/api/user/check/heat-installed", "/api/user/check/sse"}
    counts = Counter((c.method, c.path) for c in calls if c.path.startswith("/api/user/check/"))
    for endpoint in polled:
        assert counts[("GET", endpoint)] == 1, f"{endpoint}: {counts[('GET', endpoint)]} начальных опросов"

    # Установка Heat (кнопки внутри .plugin-required).
    await page.locator(".plugin-required .install-btn").nth(0).click()
    await page.wait_for_timeout(150)
    assert ("GET", "/api/user/install-heat") in [(c.method, c.path) for c in calls]

    # «Да кто такой этот ваш Heat?!» — раскрытие клипа.
    await page.locator("#wtf-is-heat-link").click()
    assert await page.locator("#wtf-is-heat-clip").is_visible()


# ── /panel/other ────────────────────────────────────────────────────────────


async def test_other_reset_secret_and_steam(front_page: Page, front_server_url: str) -> None:
    page = front_page
    calls = await _open(page, front_server_url, "/panel/other")

    # Сброс ключа оверлеев.
    await page.locator("#reset-overlay-secret-btn").click()
    await page.wait_for_timeout(150)
    assert ("POST", "/api/user/overlay/reset-secret") in [(c.method, c.path) for c in calls]

    # Отвязка Steam: запрос + кнопка «Привязать» (ссылка) проявляется
    # (мок-юзер со steam_id из каталога).
    await page.locator("#steam-unlink-btn").click()
    await page.wait_for_timeout(150)
    assert ("POST", "/api/user/steam/unlink") in [(c.method, c.path) for c in calls]
    assert await page.locator("#steam-unlink-btn").is_hidden()
    assert await page.locator("#steam-link").is_visible()


# ── Меню пользователя / баланс ──────────────────────────────────────────────


def test_user_menu_balance_history_item() -> None:
    """«История баланса» в меню только при ненулевом балансе; ссылка
    AI-стикеров ведёт на новый путь раздела панели."""
    from tests.front.pages import TEMPLATES_ENV, MockRequest, make_user

    def render(balance: int) -> str:
        return TEMPLATES_ENV.get_template("base.html").render(
            request=MockRequest("http://mock/"), user=make_user(balance=balance)
        )

    assert "/balance-history" in render(50)
    assert "/panel/ai-stickers" in render(50)

    zero = render(0)
    assert "/balance-history" not in zero


async def test_balance_history_banner(front_page: Page, front_server_url: str) -> None:
    await front_page.goto(front_server_url.rstrip("/") + "/balance-history")
    banner = front_page.locator(".balance-current-banner")
    assert await banner.is_visible()
    assert "100" in (await banner.text_content() or "")


# ── Смоуки прочих страниц ───────────────────────────────────────────────────


async def test_main_page_smoke(front_page: Page, front_server_url: str) -> None:
    await front_page.goto(front_server_url)
    await front_page.wait_for_selector("#twitchLoginBtn")


async def test_ui_demo_smoke(front_page: Page, front_server_url: str) -> None:
    await front_page.goto(front_server_url + "ui-demo")
    await front_page.wait_for_function("!!customElements.get('q0ui-switch')", timeout=15_000)
    assert await front_page.locator("q0ui-switch").count() >= 3
