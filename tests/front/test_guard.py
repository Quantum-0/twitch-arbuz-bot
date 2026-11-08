"""Самотест guard'а внешних вызовов: реальный запрос в twitch API перехватывается,
наружу не улетает и фиксируется (именно эта фиксация роняет обычные тесты)."""

from playwright.async_api import Page


async def test_guard_intercepts_real_external_api_call(front_page: Page, front_server_url: str) -> None:
    page = front_page
    await page.goto(front_server_url + "blank")

    # Пытаемся дернуть настоящий twitch-API, memealerts и ai-tunnel со страницы.
    result = await page.evaluate(
        """async () => {
            const targets = [
                "https://api.twitch.tv/helix/users",
                "https://memealerts.ru/api/v1/user",
                "https://ai-tunnel.ru/v1/images",
            ];
            const out = [];
            for (const url of targets) {
                try {
                    await fetch(url);
                } catch (e) {
                    out.push(url + " -> " + e.message);
                }
            }
            return out;
        }""",
    )
    # fetch не бросил сетевых ошибок — guard ответил заглушкой, наружу ничего не ушло.
    assert result == [], f"fetch наружу не должен падать сетевой ошибкой (guard отвечает стабом): {result}"

    recorded = list(page._front_external_calls)  # type: ignore[attr-defined]  # noqa: SLF001
    try:
        assert len(recorded) == 3, f"guard должен был зафиксировать все 3 внешних вызова: {recorded}"
        assert any("api.twitch.tv" in call for call in recorded)
        assert any("memealerts.ru" in call for call in recorded)
        assert any("ai-tunnel.ru" in call for call in recorded)
    finally:
        # Чистим фиксации: этот тест ПРОВЕРЯЕТ guard, а не нарушает изоляцию.
        page._front_external_calls.clear()  # type: ignore[attr-defined]  # noqa: SLF001
