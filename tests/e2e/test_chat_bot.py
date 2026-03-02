from time import sleep

import allure
import pytest
from playwright.sync_api import Page

from tests.e2e.utils import screenshot


@pytest.mark.skip(reason="Not finished")
@allure.title("Проверка работы чат-бота")
def test_check_main_page(context, main_page_url, twitch_creds):
    page: Page = context.new_page()
    with allure.step("Открываем панель управления ботом"):
        page.goto(main_page_url)
        page.wait_for_load_state(state="networkidle")

    enable_chat_bot_toggle = page.locator('[data-name="enable_chat_bot"]')
    active = "active" in enable_chat_bot_toggle.get_attribute("class").split()

    with allure.step("Выключаем чат бота, если он включён"):
        if active:
            enable_chat_bot_toggle.click()
            sleep(3)
        scr = screenshot(page, full_page=False)
        allure.attach(scr, name="Чат бот выключен", attachment_type=allure.attachment_type.PNG)

    with (allure.step("Заходим на твич")):
        page_twitch: Page = context.new_page()
        page_twitch.goto("https://www.twitch.tv/quantum075", timeout=60000)
        page_twitch.click("text=Войти")
        page_twitch.locator('[data-test-selector="login-username-input"]').type(twitch_creds[0])
        page_twitch.locator('[data-test-selector="login-password-input"]').type(twitch_creds[1])
        page_twitch.click("text=Чат")

        scr = screenshot(page, full_page=False)
        allure.attach(scr, name="Зашли на твич, открыли чат", attachment_type=allure.attachment_type.PNG)

        # chat_input = page.get_by_role('textbox')
        chat_input = page_twitch.locator('[data-test-selector="chat-input"]')
        chat_input.focus()
        page_twitch.locator('[data-test-selector="chat-rules-ok-button"]').click()
        chat_input.type("!cmdlist")
        chat_input.press('Enter')