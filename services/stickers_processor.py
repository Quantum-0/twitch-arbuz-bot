import asyncio
import io
from typing import AsyncIterator, Optional

import numpy as np
from PIL import Image, ImageFilter
from rembg import remove, new_session
from scipy.ndimage import binary_dilation, distance_transform_edt, label


class StickerProcessor:
    def __init__(self, max_queue_size: int = 100):
        self.session = new_session("isnet-anime") # u2net

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

        # Диапазон зеленого цвета в PIL HSV (H: ~35-105, S: >50, V: >50)
        green_mask = (h >= 65) & (h <= 105) & (s > 75) & (v > 75)
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

            if not self._has_green_background(img):
                # Если не подходит под условия — возвращаем оригинал БЕЗ обработки
                return image_bytes

            # 1. Удаляем фон с помощью rembg
            ai_output = remove(
                img,
                session=self.session,
            )

            data = np.array(img)
            ai_mask = np.array(ai_output)[:, :, 3]

            # 3. Ищем ядовитый хромакейный цвет
            r, g, b = data[:, :, 0], data[:, :, 1], data[:, :, 2]
            raw_green_mask = (g > 1.3 * r) & (g > 1.3 * b) & (g > 90)

            # --- УМНЫЙ ФИЛЬТР ДЕТАЛЕЙ ---
            labeled_mask, num_features = label(raw_green_mask)
            feature_sizes = np.bincount(labeled_mask.ravel())
            min_area_size = 1500

            chromakey_mask = np.zeros_like(raw_green_mask)
            for i in range(1, num_features + 1):
                if feature_sizes[i] >= min_area_size:
                    chromakey_mask[labeled_mask == i] = True
            # --------------------------------------------------

            # Расширяем зону фона для поиска грязных контуров
            structure = np.ones((5, 5), dtype=bool)
            extended_green_mask = binary_dilation(chromakey_mask, structure=structure)

            # Выделяем только ГРАНИЧНЫЕ полупрозрачные пиксели для исправления
            edge_green_pixels = extended_green_mask & (ai_mask < 250) & (ai_mask > 15)

            # 4. АДАПТИВНОЕ ИСПРАВЛЕНИЕ ЦВЕТА
            # Персонажем считаем всё, что нейросеть уверенно вырезала И что не является фоном
            clean_character_mask = (ai_mask > 240) & ~chromakey_mask

            # Если вдруг маска персонажа пустая, делаем заглушку, чтобы distance_transform не упал
            if not np.any(clean_character_mask):
                clean_character_mask = (ai_mask > 0)

            indices = distance_transform_edt(~clean_character_mask, return_distances=False, return_indices=True)
            nearest_y = indices[0]
            nearest_x = indices[1]

            # Перекрашиваем только если нашли пиксели для исправления
            if np.any(edge_green_pixels):
                base_r = data[nearest_y[edge_green_pixels], nearest_x[edge_green_pixels], 0]
                base_g = data[nearest_y[edge_green_pixels], nearest_x[edge_green_pixels], 1]
                base_b = data[nearest_y[edge_green_pixels], nearest_x[edge_green_pixels], 2]

                darken_factor = 0.65
                data[edge_green_pixels, 0] = np.clip(base_r * darken_factor, 0, 255).astype(np.uint8)
                data[edge_green_pixels, 1] = np.clip(base_g * darken_factor, 0, 255).astype(np.uint8)
                data[edge_green_pixels, 2] = np.clip(base_b * darken_factor, 0, 255).astype(np.uint8)

            # 5. Собираем финальную прозрачность персонажа
            # Убираем только явный хромакей, остальное доверяем rembg
            final_alpha = np.where(chromakey_mask & (ai_mask < 128), 0, ai_mask)

            # --- БЛОК ИДЕАЛЬНО ГЛАДКОЙ БЕЛОЙ ОБВОДКИ ---
            stroke_thickness = 6
            anti_aliasing_softness = 1.1

            base_mask_img = Image.fromarray(final_alpha, mode="L")
            blurred_mask = base_mask_img.filter(ImageFilter.GaussianBlur(radius=stroke_thickness))

            thresh = 8
            smooth_mask = blurred_mask.point(
                lambda x: 255 if x > thresh + 10
                else (int((x - thresh) * (255 / 10) * anti_aliasing_softness) if x > thresh else 0)
            )

            height, width = final_alpha.shape
            outline_data = np.zeros((height, width, 4), dtype=np.uint8)
            outline_data[:, :, :3] = 255
            outline_data[:, :, 3] = np.array(smooth_mask)

            character_data = data.copy()
            character_data[:, :, 3] = final_alpha

            outline_img = Image.fromarray(outline_data)
            character_img = Image.fromarray(character_data)
            final_result = Image.alpha_composite(outline_img, character_img)

            # --- БЛОК ДОБАВЛЕНИЯ ТЕНИ ---
            shadow_offset = (5, 8)
            shadow_blur = 12
            shadow_opacity = 0.4

            shadow_layer = Image.new("RGBA", final_result.size, (0, 0, 0, 0))
            shadow_mask = Image.fromarray(np.array(smooth_mask), mode="L")
            shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
            shadow_mask = shadow_mask.point(lambda x: int(x * shadow_opacity))

            shadow_color = Image.new("RGBA", final_result.size, (0, 0, 0, 255))
            shadow_layer.paste(shadow_color, (0, 0), shadow_mask)

            offset_shadow_layer = Image.new("RGBA", final_result.size, (0, 0, 0, 0))
            offset_shadow_layer.paste(shadow_layer, shadow_offset)
            final_result = Image.alpha_composite(offset_shadow_layer, final_result)

            # Сохраняем результат в байты
            output = io.BytesIO()
            final_result.save(output, format="PNG", optimize=True)
            return output.getvalue()
        finally:
            for name in (
                "img",
                "ai_output",
                "base_mask_img",
                "blurred_mask",
                "smooth_mask",
                "outline_img",
                "character_img",
                "shadow_layer",
                "shadow_mask",
                "shadow_color",
                "offset_shadow_layer",
                "final_result",
            ):
                obj = locals().get(name)
                if isinstance(obj, Image.Image):
                    obj.close()

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
