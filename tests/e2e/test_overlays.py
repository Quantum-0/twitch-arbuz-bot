from time import sleep

import allure
from playwright.sync_api import Page

from tests.e2e.utils import screenshot


def test_jumping_chibi_overlay(context, main_page_url):
    page: Page = context.new_page()
    with allure.step("Открываем главную страницу"):
        page.goto(main_page_url)
        page.wait_for_load_state(state="networkidle")

    with allure.step("Прокручиваем до Jumping Chibi"):
        page.locator("text=Jumping Chibi").scroll_into_view_if_needed()

    with allure.step("Делаем скриншот"):
        scr = screenshot(page, full_page=False)
        allure.attach(scr, name="Оверлеи", attachment_type=allure.attachment_type.PNG)

    with allure.step("Копируем ссылку"):
        link = page.locator('.overlay-card').filter(has_text='Jumping Chibi').locator(".overlay-link")
        link.hover()
        link.click()

    clipboard_content = page.evaluate("navigator.clipboard.readText()")
    with allure.step("Проверяем что ссылка скопировалась"):
        assert '/overlay/jumping-chibi' in clipboard_content

    with allure.step("Открываем оверлей"):
        page.goto(clipboard_content)
        page.wait_for_load_state(state="networkidle")

    with allure.step("Делаем скриншот"):
        sleep(3)
        scr = screenshot(page, full_page=False)
        allure.attach(scr, name="Запущенный оверлей", attachment_type=allure.attachment_type.PNG)

    page.close()