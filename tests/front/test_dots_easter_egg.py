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

import math
import time

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


async def _play(page: Page, seconds: int, from_tick: int = 0) -> None:
    """«Играть» заданное число секунд: взмах-пауза каждую секунду (куча застаблена)."""
    for tick in range(from_tick, from_tick + seconds):
        await _wiggle(page, tick)
        await page.clock.fast_forward(1000)


async def test_afk_after_playing_does_not_trigger(front_page: Page, front_server_url: str) -> None:
    """Регрессия: повозился мышкой с точками и ушёл — пасхалки быть не должно."""
    await _open(front_page, front_server_url)

    # Путь >150px за окно (ACTIVITY_DISTANCE_PX), куча собрана — сбор «активен».
    for x in range(200, 500, 30):
        await front_page.mouse.move(x, 300)

    # Пять минут мышь неподвижна (пользователь ушёл от компьютера).
    await _advance(front_page, 5 * 60)

    assert not await front_page.evaluate(_TRIGGERED)


async def test_afk_mid_game_does_not_trigger(front_page: Page, front_server_url: str) -> None:
    """Худший случай AFK: уход посреди настоящей игры с накопленной выдержкой.

    40 секунд живых циклов «взмах — пауза», затем мышь замерла на 5 минут.
    Активность выгорает вместе с окном (15с) + grace (2с) → выдержка
    рассыпается задолго до 60с. На исходном коде (замороженный moveSum)
    пасхалка бы сработала — это и есть регрессия ложного срабатывания.
    """
    await _open(front_page, front_server_url)

    for cycle in range(5):  # 5 циклов «burst + пауза 8с» = 40с игры
        base = 200 + (cycle % 2) * 300
        for step in range(7):
            await front_page.mouse.move(base + step * 50, 300)
        await _advance(front_page, 8)

    # Пользователь ушёл в разгар игры: пауза превращается в 5 минут простоя.
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

    await _play(front_page, 30)
    await front_page.mouse.click(300, 300)  # pointerdown → reset()
    await _play(front_page, 40, from_tick=30)

    assert not await front_page.evaluate(_TRIGGERED)


async def test_scroll_resets_sustain(front_page: Page, front_server_url: str) -> None:
    """Скролл — обычное чтение страницы, а не игра: сбрасывает выдержку."""
    await _open(front_page, front_server_url)

    await _play(front_page, 30)
    await front_page.evaluate("window.dispatchEvent(new Event('scroll'))")
    await _play(front_page, 40, from_tick=30)

    assert not await front_page.evaluate(_TRIGGERED)


async def test_keydown_resets_sustain(front_page: Page, front_server_url: str) -> None:
    """Клавиатура (навигация, хоткеи) — не игра: сбрасывает выдержку."""
    await _open(front_page, front_server_url)

    await _play(front_page, 30)
    await front_page.keyboard.press("a")
    await _play(front_page, 40, from_tick=30)

    assert not await front_page.evaluate(_TRIGGERED)


async def test_tab_hidden_resets_sustain(front_page: Page, front_server_url: str) -> None:
    """Свёрнутая вкладка — пользователя нет: сброс и после возврата выдержка с нуля.

    document.hidden подменяется геттером и стреляет настоящий visibilitychange —
    слушатель пасхалки читает флаг в момент события.
    """
    await _open(front_page, front_server_url)

    await _play(front_page, 30)
    await front_page.evaluate(
        """() => {
            Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
            document.dispatchEvent(new Event('visibilitychange'));
            Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
            document.dispatchEvent(new Event('visibilitychange'));
        }"""
    )
    await _play(front_page, 40, from_tick=30)

    assert not await front_page.evaluate(_TRIGGERED)


async def test_window_blur_resets_sustain(front_page: Page, front_server_url: str) -> None:
    """Переключение окна (alt-tab) — пользователя нет за игрой: сброс выдержки."""
    await _open(front_page, front_server_url)

    await _play(front_page, 30)
    await front_page.evaluate("window.dispatchEvent(new Event('blur'))")
    await _play(front_page, 40, from_tick=30)

    assert not await front_page.evaluate(_TRIGGERED)


async def test_bursts_with_pauses_trigger(front_page: Page, front_server_url: str) -> None:
    """Живой жест игры: поводить — остановиться полюбоваться кучей — поводить.

    Регрессия: гейт «движение не старше 5с» ронял условие в каждой паузе
    дольше ~5с и выдержка сбрасывалась каждый цикл — пасхалка перестала
    срабатывать вовсе. Паузы короче окна активности должны моститься.
    """
    await _open(front_page, front_server_url)

    for cycle in range(12):  # 12 циклов «burst + 8с паузы» = 96с фейкового времени
        base = 200 + (cycle % 2) * 300
        for step in range(7):  # размашистый взмах ~300px
            await front_page.mouse.move(base + step * 50, 300)
        await _advance(front_page, 8)

    assert await front_page.evaluate(_TRIGGERED)


async def test_real_physics_play_triggers(front_page: Page, front_server_url: str) -> None:
    """Настоящая физика stars-bg — без стаба кучи и без фейковых таймеров.

    Живой жест «собирать рой и любоваться»: сперва медленный непрерывный обход
    (рой слетается за курсором — точки догоняют максимум на ~108 px/с), затем
    в одной области короткие кистевые взмахи ±45px чередуются с паузами, в
    которых куча уплотняется выше порога. Стаб-тесты выше подменяют
    nearMouseCount и физики не видят — этот единственный проверяет, что
    естественный жест реально запускает пасхалку. Реальное время ~80с.
    """
    page = front_page
    await page.goto(front_server_url.rstrip("/") + "/")
    await page.wait_for_load_state("load")

    def roam(t: float) -> tuple[float, float]:
        return (640 + 300 * math.sin(0.25 * t), 360 + 180 * math.sin(0.4 * t + 1.0))

    # Фаза сбора: медленный обход, пока куча не нарастёт с запасом над порогом
    # (позиции точек случайны — собираем адаптивно, но не дольше 30с).
    t = 0.0
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        await page.mouse.move(*roam(t))
        await page.wait_for_timeout(250)
        t += 0.25
        if t >= 8 and await page.evaluate("window.starsBg.nearMouseCount(90)") >= 18:
            break
    home_x, home_y = roam(t)

    # Фаза игры: взмах 1.5с (6 перемещений ~90px) + пауза 4с на любование.
    for _ in range(14):
        for k in range(6):
            await page.mouse.move(home_x + (45 if k % 2 == 0 else -45), home_y + k * 5)
            await page.wait_for_timeout(150)
        if await page.evaluate(_TRIGGERED):
            return
        await page.wait_for_timeout(4000)

    assert await page.evaluate(_TRIGGERED)
