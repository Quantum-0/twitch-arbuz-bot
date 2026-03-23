from time import sleep

import allure
from playwright.async_api import Page

from tests.e2e.utils import screenshot

@allure.epic("Front-end E2E")
@allure.feature("Unauthorized Access")
@allure.story("Main page")
@allure.title("Главная страница, неавторизованный пользователь")
def test_check_main_page(context_unauthorized, main_page_url):
    page: Page = context_unauthorized.new_page()
    with allure.step("Открываем главную страницу"):
        page.goto(main_page_url)
        page.wait_for_load_state(state="networkidle")

    with allure.step("Делаем скриншот"):
        scr = screenshot(page)
        allure.attach(scr, name="Главная страница, светлая тема, неавторизованный пользователь", attachment_type=allure.attachment_type.PNG)

    with allure.step("Меняем тему оформления на тёмную"):
        page.locator('#themeToggle').click()
        page.wait_for_load_state(state="networkidle")
        sleep(2)

    with allure.step("Делаем скриншот с тёмной темой"):
        scr = screenshot(page)
        allure.attach(scr, name="Главная страница, тёмная тема, неавторизованный пользователь", attachment_type=allure.attachment_type.PNG)

    with allure.step("Меняем тему оформления обратно на светлую"):
        page.locator('#themeToggle').click()

    page.close()

@allure.title("Авторизация")
def test_auth(context_unauthorized, main_page_url):
    page: Page = context_unauthorized.new_page()
    with allure.step("Открываем главную страницу"):
        page.goto(main_page_url)
        page.wait_for_load_state()

    with allure.step("Наводим мышь на кнопку авторизации"):
        scr = screenshot(page)
        allure.attach(scr, name="Главная страница", attachment_type=allure.attachment_type.PNG)
        page.hover("text=Login with Twitch")
        scr = screenshot(page)
        allure.attach(scr, name="Навели мышь на кнопку", attachment_type=allure.attachment_type.PNG)

    with allure.step("Жмём кнопку авторизации"):
        page.click("text=Login with Twitch")
        page.wait_for_load_state()
        assert "https://www.twitch.tv/login" in page.url
        scr = screenshot(page)
        allure.attach(scr, name="Перешли в Twitch для авторизации", attachment_type=allure.attachment_type.PNG)

    page.close()

@allure.title("Переход в `О сервисе`")
def test_goto_about(context_unauthorized, main_page_url):
    page: Page = context_unauthorized.new_page()
    with allure.step("Открываем главную страницу"):
        page.goto(main_page_url)
        page.wait_for_load_state()

    with allure.step("Наводим мышь на кнопку О Сервисе"):
        scr = screenshot(page)
        allure.attach(scr, name="Главная страница", attachment_type=allure.attachment_type.PNG)
        page.hover("text=О сервисе")
        scr = screenshot(page)
        allure.attach(scr, name="Навели мышь на кнопку", attachment_type=allure.attachment_type.PNG)

    with allure.step("Жмём кнопку"):
        page.click("text=О сервисе")
        sleep(3)
        # page.wait_for_load_state()
        assert "/about" in page.url
        scr = screenshot(page)
        allure.attach(scr, name="Перешли на /about", attachment_type=allure.attachment_type.PNG)

    with allure.step("Возвращаемся на главную"):
        page.click("text=Главная")
        page.wait_for_load_state()
        assert page.url == main_page_url

    page.close()

@allure.title("Переход в `Стримеры`")
def test_goto_streamers(context_unauthorized, main_page_url):
    page: Page = context_unauthorized.new_page()
    with allure.step("Открываем главную страницу"):
        page.goto(main_page_url)
        page.wait_for_load_state()

    with allure.step("Наводим мышь на кнопку О Сервисе"):
        scr = screenshot(page)
        allure.attach(scr, name="Главная страница", attachment_type=allure.attachment_type.PNG)
        page.hover("text=Стримеры")
        scr = screenshot(page)
        allure.attach(scr, name="Навели мышь на кнопку", attachment_type=allure.attachment_type.PNG)

    with allure.step("Жмём кнопку"):
        page.click("text=Стримеры")
        page.wait_for_load_state()
        assert "/streamers" in page.url
        scr = screenshot(page)
        allure.attach(scr, name="Перешли на /streamers", attachment_type=allure.attachment_type.PNG)

    with allure.step("Возвращаемся на главную"):
        page.click("text=Главная")
        page.wait_for_load_state()
        assert page.url == main_page_url

    page.close()

@allure.title("Переход в `Ресайзер`")
def test_goto_resizer(context_unauthorized, main_page_url):
    page: Page = context_unauthorized.new_page()
    with allure.step("Открываем главную страницу"):
        page.goto(main_page_url)
        page.wait_for_load_state()

    with allure.step("Жмём кнопку"):
        page.click("text=Ресайзер иконок для твича")
        page.wait_for_load_state()
        assert page.url == "https://resizer.quantum0.ru/"
        scr = screenshot(page)
        allure.attach(scr, name="Перешли на ресайзер", attachment_type=allure.attachment_type.PNG)

    page.close()


@allure.title("Список стримеров без авторизации")
def test_streamers_list(context_unauthorized, main_page_url):
    page: Page = context_unauthorized.new_page()
    with allure.step("Открываем список стримеров"):
        page.goto(main_page_url + "streamers")
        page.wait_for_load_state()

    with allure.step("Проверяем что элементы присутствуют"):
        count: int = page.locator(".streamer-container").count()  # noqa
        assert count > 10
        scr = screenshot(page)
        allure.attach(scr, name="Список стримеров", attachment_type=allure.attachment_type.PNG)

    with allure.step("Меняем тему оформления на тёмную"):
        page.locator('#themeToggle').click()
        page.wait_for_load_state(state="networkidle")
        sleep(2)
        scr = screenshot(page)
        allure.attach(scr, name="Скриншот с тёмной темой", attachment_type=allure.attachment_type.PNG)

    with allure.step("Меняем тему оформления обратно на светлую"):
        page.locator('#themeToggle').click()

    page.close()


@allure.title("О сервисе без авторизации")
def test_about(context_unauthorized, main_page_url):
    page: Page = context_unauthorized.new_page()
    with allure.step("Открываем страничку о сервисе"):
        page.goto(main_page_url + "about")
        page.wait_for_load_state()

    with allure.step("Проверяем что элементы присутствуют"):
        count: int = page.locator(".author-card").count()  # noqa
        assert count > 0
        count: int = page.locator(".tester-card").count()  # noqa
        assert count > 0
        scr = screenshot(page)
        allure.attach(scr, name="О сервисе", attachment_type=allure.attachment_type.PNG)

    with allure.step("Меняем тему оформления на тёмную"):
        page.locator('#themeToggle').click()
        page.wait_for_load_state(state="networkidle")
        sleep(2)
        scr = screenshot(page)
        allure.attach(scr, name="Скриншот с тёмной темой", attachment_type=allure.attachment_type.PNG)

    with allure.step("Меняем тему оформления обратно на светлую"):
        page.locator('#themeToggle').click()

    page.close()


@allure.title("Список команд у стримера")
def test_cmdlist(context_unauthorized, main_page_url):
    page: Page = context_unauthorized.new_page()
    with allure.step("Открываем страничку о сервисе"):
        page.goto(main_page_url + "cmdlist?streamer=quantum075")
        page.wait_for_load_state()

    with allure.step("Проверяем что элементы присутствуют"):
        count: int = page.locator("tr").count()  # noqa
        assert count > 5
        scr = screenshot(page)
        allure.attach(scr, name="Список команд", attachment_type=allure.attachment_type.PNG)

    with allure.step("Меняем тему оформления на тёмную"):
        page.locator('#themeToggle').click()
        page.wait_for_load_state(state="networkidle")
        sleep(2)
        scr = screenshot(page)
        allure.attach(scr, name="Скриншот с тёмной темой", attachment_type=allure.attachment_type.PNG)

    with allure.step("Меняем тему оформления обратно на светлую"):
        page.locator('#themeToggle').click()

    page.close()


@allure.title("Не существующая страница")
def test_not_found(context_unauthorized, main_page_url):
    page: Page = context_unauthorized.new_page()

    with allure.step("Открываем страницу которой не существует"):
        page.goto(main_page_url + "404")
        page.wait_for_load_state()

    assert page.inner_text("body") == '{"detail":"Not Found"}'

    page.close()

# TODO:
#  - НА БУДУЩЕЕ: /profile/quantum075
