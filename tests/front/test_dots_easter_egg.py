"""Браузерные тесты пасхалки «сборщик точек» (static/js/dots-easter-egg.js).

Фейковые таймеры page.clock проматывают 60-секундную выдержку мгновенно.
Куча точек у курсора стабится подменой window.starsBg.nearMouseCount (порог
15), движение курсора — реальные события page.mouse.move (настоящие
pointermove), так что тестируется именно логика пасхалки, а не физика точек.

Главный сценарий — регрессия AFK: окно активности (moves/moveSum) раньше
чистилось только внутри обработчика pointermove, поэтому замерший курсор
навсегда сохранял накопленный путь — и пасхалка приглушала интерфейс у
ушедшего от компьютера пользователя.
"""

from playwright.async_api import Page

# Куча точек всегда достаточна для порога COLLECT_THRESHOLD = 15.
_PILE_STUB = "window.starsBg.nearMouseCount = () => 20"
_TRIGGERED = "document.body.classList.contains('dots-easter-egg')"


async def _open(page: Page, server_url: str) -> None:
    """Главная страница с фейковыми таймерами (устанавливаются ДО навигации)."""
    await page.clock.install()
    await page.goto(server_url.rstrip("/") + "/")
    # load = все defer-скрипты (stars-bg.js, dots-easter-egg.js) выполнились.
    await page.wait_for_load_state("load")
    await page.evaluate(_PILE_STUB)


async def _advance(page: Page, seconds: int) -> None:
    """Промотать фейковое время по секундам.

    fast_forward стреляет каждым таймером максимум один раз на вызов, поэтому
    мотаем шагами по секунде: проверка пасхалки (интервал 500мс) обязана
    сработать на каждом шаге. run_for не подходит — он фейкает и rAF, и пять
    минут покадрового рендера stars-bg занимают десятки секунд.
    """
    for _ in range(seconds):
        await page.clock.fast_forward(1000)


async def _wiggle(page: Page, tick: int) -> None:
    """Пара настоящих pointermove (~80px пути) — курсор «играет» прямо сейчас."""
    x = 300 + (40 if tick % 2 == 0 else -40)
    await page.mouse.move(x, 300)
    await page.mouse.move(300, 300)


async def test_afk_after_playing_does_not_trigger(front_page: Page, front_server_url: str) -> None:
    """Регрессия: повозился мышкой с точками и ушёл — пасхалки быть не должно."""
    await _open(front_page, front_server_url)

    # Путь >150px за окно (ACTIVITY_DISTANCE_PX), куча собрана — сбор «активен».
    for x in range(200, 500, 30):
        await front_page.mouse.move(x, 300)

    # Пять минут мышь неподвижна (пользователь ушёл от компьютера).
    await _advance(front_page, 5 * 60)

    assert not await front_page.evaluate(_TRIGGERED)


async def test_idle_mouse_never_triggers(front_page: Page, front_server_url: str) -> None:
    """Куча у неподвижного курсора (точки сами натекают) — это не игра."""
    await _open(front_page, front_server_url)

    await _advance(front_page, 5 * 60)

    assert not await front_page.evaluate(_TRIGGERED)


async def test_continuous_play_triggers(front_page: Page, front_server_url: str) -> None:
    """Легитимный сценарий жив: непрерывная игра ~60с всё ещё срабатывает."""
    await _open(front_page, front_server_url)

    for tick in range(70):  # выдержка SUSTAIN_MS = 60с, запас на дискретность
        await _wiggle(front_page, tick)
        await front_page.clock.fast_forward(1000)

    assert await front_page.evaluate(_TRIGGERED)


async def test_click_resets_sustain(front_page: Page, front_server_url: str) -> None:
    """Клик сбрасывает выдержку: 30с игры + клик + 40с игры < непрерывных 60с."""
    await _open(front_page, front_server_url)

    for tick in range(30):
        await _wiggle(front_page, tick)
        await front_page.clock.fast_forward(1000)
    await front_page.mouse.click(300, 300)  # pointerdown → reset()
    for tick in range(30, 70):
        await _wiggle(front_page, tick)
        await front_page.clock.fast_forward(1000)

    assert not await front_page.evaluate(_TRIGGERED)
