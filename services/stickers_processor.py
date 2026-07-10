import asyncio
import io
from typing import AsyncIterator, Optional

import numpy as np
from PIL import Image, ImageFilter
from rembg import new_session, remove


class StickerProcessor:
    def __init__(self, max_queue_size: int = 100):
        self.session = new_session("silueta")

        # Очередь для входящих задач и ограничение ее размера
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self.worker_task: Optional[asyncio.Task] = None
        self.is_running = False

    async def start(self):
        """Запуск фонового воркера, который обрабатывает задачи по очереди"""
        if not self.is_running:
            self.is_running = True
            self.worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self):
        """Корректная остановка воркера"""
        self.is_running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

    def _has_green_background(self, img: Image.Image, threshold_pct: float = 0.1) -> bool:
        """
        Быстрая проверка: является ли фон зеленым.
        Переводим в HSV, так как там проще выделить зеленый цвет.
        """
        # Уменьшаем копию изображения для мгновенного анализа цвета (экономим ОЗУ и CPU)
        small_img = img.resize((100, 100))
        hsv = np.array(small_img.convert("HSV"))
        h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

        # Диапазон зеленого цвета в PIL HSV (H: ~35-85, S: >50, V: >50)
        green_mask = (h >= 35) & (h <= 85) & (s > 50) & (v > 50)
        green_pixels = np.sum(green_mask)

        # Если зеленых пикселей больше, чем threshold_pct от всей картинки
        return (green_pixels / 10000.0) >= threshold_pct

    def _process_image_sync(self, image_bytes: bytes) -> bytes:
        """
        Синхронная "тяжелая" функция обработки.
        Выполняется в отдельном потоке, чтобы не блокировать сервер.
        """
        try:
            # Открываем изображение
            img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

            # Проверяем, квадратное ли оно и есть ли зеленый фон
            is_square = img.width == img.height
            if not is_square or not self._has_green_background(img):
                # Если не подходит под условия — возвращаем оригинал БЕЗ обработки
                return image_bytes

            # 1. Удаляем фон с помощью rembg
            no_bg = remove(img, session=self.session)

            # Разделяем на каналы, нам нужна маска альфа-канала
            alpha = no_bg.split()[3]

            # 2. Создаем обводку (белую)
            stroke_size = 10
            # Размываем маску и делаем ее жесткой, чтобы расширить края
            stroke_mask = alpha.filter(ImageFilter.MaxFilter(stroke_size * 2 + 1))
            stroke_mask = stroke_mask.point(lambda p: 255 if p > 0 else 0)

            white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            with_stroke = Image.composite(white_bg, Image.new("RGBA", img.size, (0, 0, 0, 0)), stroke_mask)
            with_stroke.alpha_composite(no_bg)

            # 3. Создаем мягкую тень (черную)
            shadow_offset = (5, 5)
            shadow_blur = 15

            # Снова берем маску обводки, чтобы тень шла от нее
            shadow_mask = stroke_mask.filter(ImageFilter.GaussianBlur(shadow_blur))
            # Делаем тень полупрозрачной (например, 40% непрозрачности)
            shadow_mask = shadow_mask.point(lambda p: int(p * 0.4))

            shadow_bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
            shadow = Image.composite(shadow_bg, Image.new("RGBA", img.size, (0, 0, 0, 0)), shadow_mask)

            # Финальная сборка: накладываем тень со сдвигом, а сверху — стикер с обводкой
            final_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
            final_img.paste(shadow, shadow_offset, shadow)
            final_img.alpha_composite(with_stroke)

            # Сохраняем результат в байты
            output = io.BytesIO()
            final_img.save(output, format="PNG", optimize=True)
            return output.getvalue()

        finally:
            # Принудительно очищаем локальные тяжелые объекты в памяти
            if 'img' in locals(): img.close()
            if 'no_bg' in locals(): no_bg.close()
            if 'final_img' in locals(): final_img.close()

    async def _worker_loop(self):
        """Бесконечный цикл, обрабатывающий строго по одной задаче из очереди"""
        while self.is_running:
            # Ждем, пока в очереди появится задача (не нагружает CPU в ожидании)
            future, image_bytes = await self.queue.get()
            try:
                # Запускаем тяжелую синхронную обработку в отдельном системном потоке (ThreadPool).
                # Это гарантирует, что сетевой async-код FastAPI НЕ заблокируется!
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, self._process_image_sync, image_bytes)

                # Возвращаем результат обратно в конкретный API-запрос
                future.set_result(result)
            except Exception as e:
                future.set_exception(e)
            finally:
                self.queue.task_done()

    async def process(self, image_stream: AsyncIterator[bytes] | bytes) -> bytes:
        """
        Основной асинхронный метод для вызова из API.
        Принимает асинхронный итератор байт, собирает их и ставит в очередь.
        """
        if isinstance(image_stream, bytes):
            image_bytes = image_stream
        else:
            # Собираем чанки байт из асинхронного потока в один массив
            chunks = []
            async for chunk in image_stream:
                chunks.append(chunk)
            image_bytes = b"".join(chunks)

        if not image_bytes:
            raise ValueError("Empty image data")

        # Создаем объект "будущего результата" для связи запроса и воркера
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        # Кладем задачу в очередь. Если очередь полна (наплыв > 100 человек),
        # запрос будет асинхронно ждать свободного места, не тратя память.
        await self.queue.put((future, image_bytes))

        # Ждем, пока воркер обработает именно нашу картинку
        return await future
