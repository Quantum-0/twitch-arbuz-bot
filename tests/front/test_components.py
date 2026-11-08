"""Прогон харнесса библиотеки q0ui (tests/front/static/components.html):
события, клавиатура, клампы, тики, lastStatus-гонка, кнопка награды.
Ошибки консоли и внешние вызовы роняет фикстура front_page."""

from playwright.async_api import Page


async def test_q0ui_harness(front_page: Page, front_server_url: str) -> None:
    page = front_page
    await page.goto(front_server_url + "__front/components.html")
    await page.wait_for_function("window.__harnessDone !== undefined", timeout=30_000)

    done = await page.evaluate("window.__harnessDone")
    assert done["failed"] == 0, f"Упали проверки харнесса ({done['failed']}/{done['total']}): " + "\n".join(
        done["failedNames"],
    )
    assert done["total"] >= 20, "харнесс подозрительно мало проверок выполнил"
