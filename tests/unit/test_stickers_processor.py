# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты StickerProcessor (services/stickers_processor.py).

rembg подменяется фейковым модулем через sys.modules (ленивый импорт внутри
методов). Проверяются оба пайплайна: старый «зелёный» (хромакей + rembg) и
новый (обводка по готовому альфа-каналу gpt-image-2.5-*), плюс воркер-очередь.
"""

import asyncio
import io
import sys
import types

import numpy as np
import pytest
from PIL import Image

from config import settings
from services.stickers_processor import StickerProcessor


# No-op переопределение autouse-фикстуры session_override: этим тестам БД не нужна.
@pytest.fixture
def session_override() -> None:
    return None


@pytest.fixture
def rembg_disabled(monkeypatch):
    monkeypatch.setattr(settings.ai, "sticker_rembg_enabled", False)


def make_png(
    size=(100, 100),
    bg=(0, 255, 0, 255),
    square=None,
    square_color=(255, 0, 0, 255),
    bg_alpha=255,
) -> bytes:
    """PNG: зелёный фон + опциональный квадрат «персонажа» (x0,y0,x1,y1)."""
    img = Image.new("RGBA", size, (*bg[:3], bg_alpha))
    if square is not None:
        x0, y0, x1, y1 = square
        for x in range(x0, x1):
            for y in range(y0, y1):
                img.putpixel((x, y), square_color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def alpha_sticker_png(square=(30, 30, 70, 70), color=(255, 0, 0, 255)) -> bytes:
    """PNG с уже прозрачным фоном (нативная прозрачность gpt-image-2.5-*)."""
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    x0, y0, x1, y1 = square
    for x in range(x0, x1):
        for y in range(y0, y1):
            img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def install_rembg(monkeypatch, remove_fn=None, new_session_fn=None):
    """Подменяем модуль rembg (лениво импортируется внутри методов процессора)."""
    fake = types.ModuleType("rembg")

    def default_remove(img, session=None, post_process_mask=False, **kwargs):
        raise AssertionError("rembg.remove вызван без подменённой реализации")

    def default_new_session(name):
        return object()

    fake.remove = remove_fn or default_remove
    fake.new_session = new_session_fn or default_new_session
    monkeypatch.setitem(sys.modules, "rembg", fake)
    return fake


def processor_without_session() -> StickerProcessor:
    p = StickerProcessor()
    # session-property блокируется на _session_ready — «разрешаем» без загрузки модели
    p._session_ready.set()
    return p


# ── _has_green_background ─────────────────────────────────────────────────


def test_has_green_background_true_for_chromakey():
    img = Image.open(io.BytesIO(make_png()))
    assert StickerProcessor._has_green_background(img)


def test_has_green_background_false_for_red():
    img = Image.open(io.BytesIO(make_png(bg=(255, 0, 0, 255))))
    assert not StickerProcessor._has_green_background(img)


def test_has_green_background_respects_threshold():
    # Меньше 50% зелёного: дефолтный порог 10% проходит, ужесточённый — нет
    img = Image.open(io.BytesIO(make_png(bg=(255, 0, 0, 255), square=(0, 0, 50, 50), square_color=(0, 255, 0, 255))))
    assert StickerProcessor._has_green_background(img, threshold_pct=0.1)
    assert not StickerProcessor._has_green_background(img, threshold_pct=0.6)


# ── _process_image_sync: условия passthrough ──────────────────────────────


def test_process_image_sync_non_square_returns_original():
    original = make_png(size=(200, 100))
    p = processor_without_session()

    assert p._process_image_sync(original) == original


def test_process_image_sync_no_green_background_returns_original():
    original = make_png(bg=(255, 0, 0, 255))
    p = processor_without_session()

    assert p._process_image_sync(original) == original


def test_process_image_sync_session_not_loaded_returns_original():
    """Нейронка не загрузилась (rembg disabled/ошибка) — оригинал без обработки."""
    original = make_png(square=(25, 25, 75, 75))
    p = processor_without_session()
    assert p.session is None

    assert p._process_image_sync(original) == original


# ── _process_image_sync: полный зелёный пайплайн с фейковым rembg ─────────


def test_process_image_sync_green_pipeline(monkeypatch):
    square = (25, 25, 75, 75)

    def fake_remove(img, session=None, post_process_mask=False, **kwargs):
        arr = np.zeros((img.height, img.width), dtype=np.uint8)
        x0, y0, x1, y1 = square
        arr[y0:y1, x0:x1] = 255
        out = np.dstack([np.array(img)[:, :, :3], arr])
        return Image.fromarray(out, "RGBA")

    install_rembg(monkeypatch, remove_fn=fake_remove)
    original = make_png(square=square)
    p = processor_without_session()
    p._session = object()

    result = p._process_image_sync(original)

    assert result != original
    img = Image.open(io.BytesIO(result))
    assert img.size == (100, 100)
    # Хромакейный фон вырезан: углы прозрачные (нижний правый не проверяем —
    # туда дотягивается размытая тень со смещением (5, 8))
    assert img.getpixel((2, 2))[3] == 0
    assert img.getpixel((97, 2))[3] == 0
    # Персонаж остался непрозрачным и красным
    r, g, b, a = img.getpixel((50, 50))
    assert a == 255
    assert r > 200
    assert g < 60
    # Белая обводка вокруг персонажа (чуть левее квадрата)
    r, g, b, a = img.getpixel((20, 50))
    assert a > 150
    assert r > 200
    assert g > 200
    assert b > 200


def test_process_image_sync_green_pipeline_keeps_small_green_details(monkeypatch):
    """Изолированные зелёные пятна < min_area_size — детали рисунка, не фон."""

    def fake_remove(img, session=None, post_process_mask=False, **kwargs):
        # Персонаж — весь кадр (маска полная, вырезать должен только хромакей)
        arr = np.full((img.height, img.width), 255, dtype=np.uint8)
        out = np.dstack([np.array(img)[:, :, :3], arr])
        return Image.fromarray(out, "RGBA")

    install_rembg(monkeypatch, remove_fn=fake_remove)
    # Зелёный фон + большой красный квадрат + мелкое зелёное пятнышко-деталь
    original = make_png(square=(10, 10, 90, 90))
    img_orig = Image.open(io.BytesIO(original)).copy()
    for x in range(40, 45):
        for y in range(40, 45):
            img_orig.putpixel((x, y), (0, 255, 0, 255))
    buf = io.BytesIO()
    img_orig.save(buf, format="PNG")
    original = buf.getvalue()

    p = processor_without_session()
    p._session = object()
    result = p._process_image_sync(original)

    img = Image.open(io.BytesIO(result))
    # Мелкая деталь (5x5 < min_area_size=50) не считается фоном: осталась видимой
    assert img.getpixel((42, 42))[3] == 255


# ── _outline_from_alpha_sync ──────────────────────────────────────────────


def test_outline_from_alpha_opaque_image_returns_original():
    original = make_png(bg=(0, 255, 0, 255))
    p = processor_without_session()

    assert p._outline_from_alpha_sync(original) == original


def test_outline_from_alpha_adds_white_outline_and_shadow():
    original = alpha_sticker_png(square=(30, 30, 70, 70))
    p = processor_without_session()

    result = p._outline_from_alpha_sync(original)

    assert result != original
    img = Image.open(io.BytesIO(result))
    assert img.size == (100, 100)
    # Фон остался прозрачным
    assert img.getpixel((2, 2))[3] == 0
    # Персонаж на месте
    r, g, b, a = img.getpixel((50, 50))
    assert a == 255
    assert r > 200
    # Белая обводка вокруг (левее квадрата)
    r, g, b, a = img.getpixel((25, 50))
    assert a > 150
    assert r > 200
    assert g > 200
    assert b > 200
    # Тень под персонажем: ниже квадрата есть полупрозрачный тёмный пиксель
    found_shadow = False
    for y in range(72, 85):
        r, g, b, a = img.getpixel((50, y))
        if 0 < a < 255 and r < 100:
            found_shadow = True
            break
    assert found_shadow


# ── Воркер и очередь ──────────────────────────────────────────────────────


async def test_outline_end_to_end_through_worker(rembg_disabled):
    p = StickerProcessor()
    await p.start()
    try:
        assert p.is_running
        assert p.worker_task is not None

        result = await p.outline(alpha_sticker_png())
    finally:
        await p.stop()

    assert not p.is_running
    img = Image.open(io.BytesIO(result))
    assert img.size == (100, 100)
    assert img.getpixel((2, 2))[3] == 0  # фон прозрачный — обработка прошла


async def test_process_passthrough_when_rembg_disabled(rembg_disabled):
    p = StickerProcessor()

    async def source():
        yield b"chunk1"
        yield b"chunk2"

    assert await p.process(source()) == b"chunk1chunk2"


async def test_outline_empty_data_raises(rembg_disabled):
    p = StickerProcessor()
    await p.start()
    try:
        with pytest.raises(ValueError, match="Empty image data"):
            await p.outline(b"")
    finally:
        await p.stop()


async def test_worker_propagates_handler_exception(rembg_disabled):
    p = StickerProcessor()
    await p.start()
    try:

        def boom(data: bytes) -> bytes:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await p._enqueue(boom, b"some-image")
    finally:
        await p.stop()


async def test_start_is_idempotent(rembg_disabled):
    p = StickerProcessor()
    await p.start()
    first_task = p.worker_task
    await p.start()

    assert p.worker_task is first_task
    await p.stop()


async def test_start_with_rembg_enabled_loads_session(monkeypatch):
    sentinel = object()
    install_rembg(monkeypatch, new_session_fn=lambda name: sentinel)
    monkeypatch.setattr(settings.ai, "sticker_rembg_enabled", True)

    p = StickerProcessor()
    await p.start()
    try:
        for _ in range(200):
            if p._session_ready.is_set():
                break
            await asyncio.sleep(0.01)
        assert p.session is sentinel
    finally:
        await p.stop()


async def test_start_rembg_load_failure_leaves_session_none(monkeypatch):
    def broken_new_session(name):
        raise RuntimeError("onnx unavailable")

    install_rembg(monkeypatch, new_session_fn=broken_new_session)
    monkeypatch.setattr(settings.ai, "sticker_rembg_enabled", True)

    p = StickerProcessor()
    await p.start()
    try:
        for _ in range(200):
            if p._session_ready.is_set():
                break
            await asyncio.sleep(0.01)
        assert p.session is None
    finally:
        await p.stop()


# ── _collect_bytes ────────────────────────────────────────────────────────


async def test_collect_bytes_passthrough():
    assert await StickerProcessor._collect_bytes(b"ready") == b"ready"


async def test_collect_bytes_concatenates_stream():
    async def chunks():
        yield b"a"
        yield b"b"
        yield b"c"

    assert await StickerProcessor._collect_bytes(chunks()) == b"abc"


async def test_stop_is_safe_without_start():
    p = StickerProcessor()
    await p.stop()
    assert p.worker_task is None
