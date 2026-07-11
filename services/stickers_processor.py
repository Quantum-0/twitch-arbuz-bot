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


# async def main():
#     serv = StickerProcessor()
#     await serv.start()
#     with open("/Users/notamedia/img.png", "rb") as image:
#         f = image.read()
#         b = bytes(f)
#         await serv.process(b)
#
# if __name__ == "__main__":
#     asyncio.run(main())
#
# import numpy as np
# from PIL import Image, ImageFilter
# from rembg import remove, new_session
# from scipy.ndimage import binary_dilation, distance_transform_edt
#
# # 1. Загружаем картинку
# input_path = '/Users/notamedia/img3.png'
# img = Image.open(input_path).convert("RGBA")
# data = np.array(img)
#
# # 2. Получаем маску от ИИ
# session = new_session("isnet-anime")
# ai_output = remove(img, session=session)
# ai_mask = np.array(ai_output)[:, :, 3]
#
# # 3. Ищем ядовитый хромакейный цвет
# r, g, b = data[:,:,0], data[:,:,1], data[:,:,2]
# chromakey_mask = (g > 1.3 * r) & (g > 1.3 * b) & (g > 90)
#
# # Расширяем зону поиска, ловя грязные смешанные пиксели контура
# structure = np.ones((5, 5), dtype=bool)
# extended_green_mask = binary_dilation(chromakey_mask, structure=structure)
#
# # Выделяем пиксели на границе, которые нужно исправить
# edge_green_pixels = extended_green_mask & (ai_mask < 254) & (ai_mask > 20)
#
# # 4. АДАПТИВНОЕ ИСПРАВЛЕНИЕ ЦВЕТА С ЗАТЕМНЕНИЕМ ЛАЙНА
# # Создаем маску "чистого" тела персонажа, где точно нет зелени
# clean_character_mask = (ai_mask > 250) & ~extended_green_mask
#
# # Вычисляем индексы ближайших чистых пикселей персонажа для каждой точки изображения
# indices = distance_transform_edt(~clean_character_mask, return_distances=False, return_indices=True)
# nearest_y, nearest_x = indices[0], indices[1]
#
# # Копируем базовый цвет ближайшего родного пикселя
# base_r = data[nearest_y[edge_green_pixels], nearest_x[edge_green_pixels], 0]
# base_g = data[nearest_y[edge_green_pixels], nearest_x[edge_green_pixels], 1]
# base_b = data[nearest_y[edge_green_pixels], nearest_x[edge_green_pixels], 2]
#
# # --- КОЭФФИЦИЕНТ ЗАТЕМНЕНИЯ ---
# # 0.65 означает, что мы оставляем 65% от исходной яркости (затемняем на 35%).
# # Вы можете менять это число: ближе к 0.5 — темнее и контрастнее, ближе к 0.8 — светлее.
# darken_factor = 0.65
#
# # Записываем затемненный цвет в проблемные пиксели контура
# # Используем np.clip, чтобы значения цвета гарантированно оставались в рамках от 0 до 255
# data[edge_green_pixels, 0] = np.clip(base_r * darken_factor, 0, 255).astype(np.uint8)
# data[edge_green_pixels, 1] = np.clip(base_g * darken_factor, 0, 255).astype(np.uint8)
# data[edge_green_pixels, 2] = np.clip(base_b * darken_factor, 0, 255).astype(np.uint8)
#
#
# # 5. Собираем финальную прозрачность
# final_alpha = np.where(chromakey_mask, 0, ai_mask)
# data[:, :, 3] = final_alpha
#
# # --- ДАЛЕЕ ИДЕТ ВАШ ПРЕДЫДУЩИЙ БЛОК ИДЕАЛЬНО ГЛАДКОЙ БЕЛОЙ ОБВОДКИ ---
# # (Используйте тот код с ImageFilter.GaussianBlur, который вам понравился)
#
#
# from PIL import ImageFilter
#
# # --- НАЧАЛО БЛОКА ИДЕАЛЬНО ГЛАДКОЙ ОБВОДКИ ---
#
# # 1. Задаем толщину обводки в пикселях (теперь регулируется через радиус размытия)
# # Для эффекта обводки в ~7px отлично подойдут эти параметры:
# stroke_thickness = 5
# anti_aliasing_softness = 1 # Мягкость самого крайнего пикселя обводки
#
# # 2. Переводим финальную маску персонажа в PIL (градации серого)
# base_mask_img = Image.fromarray(final_alpha, mode="L")
#
# # 3. Делаем сильное размытие. Оно скруглит все острые углы и зигзаги
# blurred_mask = base_mask_img.filter(ImageFilter.GaussianBlur(radius=stroke_thickness))
#
# # 4. Превращаем размытое облако в четкую закругленную обводку с анти-алиасингом.
# # Мы сдвигаем порог (threshold), чтобы маска расширилась, и добавляем мягкий край.
# thresh = 7 # Чем меньше число, тем шире обводка
# smooth_mask = blurred_mask.point(
#     lambda x: 255 if x > thresh + 10
#     else (int((x - thresh) * (255 / 10) * anti_aliasing_softness) if x > thresh else 0)
# )
#
# # 5. Создаем слой белой обводки
# height, width = final_alpha.shape
# outline_data = np.zeros((height, width, 4), dtype=np.uint8)
# outline_data[:, :, :3] = 255  # Белый цвет
# outline_data[:, :, 3] = np.array(smooth_mask)
#
# # 6. Собираем слой персонажа
# character_data = data.copy()
# character_data[:, :, 3] = final_alpha
#
# # Переводим в PIL и склеиваем слои
# outline_img = Image.fromarray(outline_data)
# character_img = Image.fromarray(character_data)
#
# final_result = Image.alpha_composite(outline_img, character_img)
#
# # 7. Сохраняем результат
# # final_result.show()
# print("Обводка стала идеально круглой и гладкой!")
#
# # --- КОНЕЦ БЛОКА БЕЛОЙ ОБВОДКИ ---
#
#
# # --- НАЧАЛО БЛОКА ДОБАВЛЕНИЯ ТЕНИ ---
#
# # 1. Настройки тени (можно менять под себя)
# shadow_offset = (5, 8)     # Смещение тени (вправо, вниз) в пикселях
# shadow_blur = 6           # Насколько тень будет мягкой и размытой
# shadow_opacity = 0.6       # Прозрачность тени: 0.0 (невидимая) до 1.0 (густая черная)
#
# # 2. Создаем слой для тени того же размера, что и финальный результат
# shadow_layer = Image.new("RGBA", final_result.size, (0, 0, 0, 0))
#
# # 3. Извлекаем маску белой обводки, чтобы сделать из нее подложку для тени
# # Тень должна в точности повторять форму нашей новой сглаженной обводки
# shadow_mask = Image.fromarray(np.array(smooth_mask), mode="L")
#
# # Смягчаем края тени с помощью размытия
# shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
#
# # Регулируем густоту тени через прозрачность
# shadow_mask = shadow_mask.point(lambda x: int(x * shadow_opacity))
#
# # 4. Заливаем слой тени черным цветом, используя размытую маску
# shadow_color = Image.new("RGBA", final_result.size, (0, 0, 0, 255))
# shadow_layer.paste(shadow_color, (0, 0), shadow_mask)
#
# # 5. Сдвигаем тень, создавая объемный эффект (как будто персонаж парит)
# # Создаем чистый холст и накладываем туда тень со смещением
# offset_shadow_layer = Image.new("RGBA", final_result.size, (0, 0, 0, 0))
# offset_shadow_layer.paste(shadow_layer, shadow_offset)
#
# # 6. Собираем всё вместе: Тень подкладываем ПОД персонажа с обводкой
# final_with_shadow = Image.alpha_composite(offset_shadow_layer, final_result)
#
# # Перезаписываем переменную, чтобы код ниже сохранил уже полную картину
# final_result = final_with_shadow
# # final_result.show()
#
# # --- КОНЕЦ БЛОКА ДОБАВЛЕНИЯ ТЕНИ ---
#
# # --- НАЧАЛО БЛОКА ТЕСТОВОГО ФОНА ---
#
# # Выберите цвет фона (раскомментируйте нужный):
# # background_color = (255, 255, 255, 255) # Чисто белый
# # background_color = (0, 200, 0, 255)     # Хромакейный зеленый
# background_color = (230, 235, 230, 255)   # Светло-серый с зеленым оттенком (идеально для теста тени!)
#
# # Create a solid background image of the same size
# test_bg = Image.new("RGBA", final_result.size, background_color)
#
# # Склеиваем: подкладываем наш фон под итоговый рисунок с тенью
# final_result = Image.alpha_composite(test_bg, final_result)
#
# final_result.show()
# # --- КОНЕЦ БЛОКА ТЕСТОВОГО ФОНА ---


#
# import numpy as np
# from PIL import Image, ImageFilter
# from rembg import remove, new_session
# from scipy.ndimage import binary_dilation, distance_transform_edt, label
#
# # 1. Загружаем картинку
# input_path = '/Users/notamedia/img3.png'
# img = Image.open(input_path).convert("RGBA")
# data = np.array(img)
#
# # 2. Получаем маску от ИИ
# session = new_session("isnet-anime")
# ai_output = remove(
#     img,
#     session=session,
#     post_process_mask=True,
#     # alpha_matting=True,
#     # alpha_matting_foreground_threshold=240,  # Защищает основные цвета персонажей
#     # alpha_matting_background_threshold=15,  # Жестко отсекает зеленый фон в петле поводка
#     # alpha_matting_erode_size=5,
# )
#
# ai_mask = np.array(ai_output)[:, :, 3]
#
# # 3. Ищем ядовитый хромакейный цвет
# r, g, b = data[:,:,0], data[:,:,1], data[:,:,2]
# raw_green_mask = (g > 1.3 * r) & (g > 1.3 * b) & (g > 90)
#
# # --- УМНЫЙ ФИЛЬТР ДЕТАЛЕЙ (ЗАЩИТА РОБОТА И ЛАП) ---
# # Находим все изолированные зелёные островки на картинке
# labeled_mask, num_features = label(raw_green_mask)
#
# # Считаем размер каждого островка в пикселях
# feature_sizes = np.bincount(labeled_mask.ravel())
#
# # Задаем минимальный размер для фоновой зоны (например, 1500 пикселей).
# # Всё, что меньше этого размера (провода, подушечки лап), код посчитает деталью рисунка.
# min_area_size = 1500
#
# # Оставляем в маске только КРУПНЫЕ зелёные области (настоящий фон и дыры вроде поводка)
# chromakey_mask = np.zeros_like(raw_green_mask)
# for i in range(1, num_features + 1):
#     if feature_sizes[i] >= min_area_size:
#         chromakey_mask[labeled_mask == i] = True
# # --------------------------------------------------
#
# # Расширяем зону поиска фона на 2 пикселя, ловя грязные контуры
# structure = np.ones((5, 5), dtype=bool)
# extended_green_mask = binary_dilation(chromakey_mask, structure=structure)
#
# # Выделяем пиксели на границе, которые нужно исправить
# edge_green_pixels = extended_green_mask & (ai_mask < 254) & (ai_mask > 20)
#
# # 4. АДАПТИВНОЕ ИСПРАВЛЕНИЕ ЦВЕТА С ЗАТЕМНЕНИЕМ ЛАЙНА
# clean_character_mask = (ai_mask > 250) & ~extended_green_mask
#
# # ПРАВИЛЬНАЯ РАСПАКОВКА: явно указываем return_distances=False
# # и разделяем результат на два независимых массива координат Y и X
# indices = distance_transform_edt(~clean_character_mask, return_distances=False, return_indices=True)
# nearest_y = indices[0] # Индексы по строкам (Y)
# nearest_x = indices[1] # Индексы по столбцам (X)
#
# # Копируем базовый цвет ближайшего родного пикселя
# base_r = data[nearest_y[edge_green_pixels], nearest_x[edge_green_pixels], 0]
# base_g = data[nearest_y[edge_green_pixels], nearest_x[edge_green_pixels], 1]
# base_b = data[nearest_y[edge_green_pixels], nearest_x[edge_green_pixels], 2]
#
# darken_factor = 0.65
# data[edge_green_pixels, 0] = np.clip(base_r * darken_factor, 0, 255).astype(np.uint8)
# data[edge_green_pixels, 1] = np.clip(base_g * darken_factor, 0, 255).astype(np.uint8)
# data[edge_green_pixels, 2] = np.clip(base_b * darken_factor, 0, 255).astype(np.uint8)
#
#
# # 5. Собираем финальную прозрачность персонажа
# final_alpha = np.where(chromakey_mask, 0, ai_mask)
#
# # --- БЛОК ИДЕАЛЬНО ГЛАДКОЙ БЕЛОЙ ОБВОДКИ ---
# stroke_thickness = 7
# anti_aliasing_softness = 1.2
#
# base_mask_img = Image.fromarray(final_alpha, mode="L")
# blurred_mask = base_mask_img.filter(ImageFilter.GaussianBlur(radius=stroke_thickness))
#
# thresh = 15
# smooth_mask = blurred_mask.point(
#     lambda x: 255 if x > thresh + 10
#     else (int((x - thresh) * (255 / 10) * anti_aliasing_softness) if x > thresh else 0)
# )
#
# height, width = final_alpha.shape
# outline_data = np.zeros((height, width, 4), dtype=np.uint8)
# outline_data[:, :, :3] = 255
# outline_data[:, :, 3] = np.array(smooth_mask)
#
# character_data = data.copy()
# character_data[:, :, 3] = final_alpha
#
# outline_img = Image.fromarray(outline_data)
# character_img = Image.fromarray(character_data)
# final_result = Image.alpha_composite(outline_img, character_img)
#
# # --- БЛОК ДОБАВЛЕНИЯ ТЕНИ ---
# shadow_offset = (5, 8)
# shadow_blur = 12
# shadow_opacity = 0.4
#
# shadow_layer = Image.new("RGBA", final_result.size, (0, 0, 0, 0))
# shadow_mask = Image.fromarray(np.array(smooth_mask), mode="L")
# shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
# shadow_mask = shadow_mask.point(lambda x: int(x * shadow_opacity))
#
# shadow_color = Image.new("RGBA", final_result.size, (0, 0, 0, 255))
# shadow_layer.paste(shadow_color, (0, 0), shadow_mask)
#
# offset_shadow_layer = Image.new("RGBA", final_result.size, (0, 0, 0, 0))
# offset_shadow_layer.paste(shadow_layer, shadow_offset)
# final_result = Image.alpha_composite(offset_shadow_layer, final_result)
#
# # --- БЛОК ТЕСТОВОГО ФОНА (Уберите или закомментируйте перед финалом) ---
# background_color = (230, 235, 230, 255) # Светло-серый
# test_bg = Image.new("RGBA", final_result.size, background_color)
# final_result = Image.alpha_composite(test_bg, final_result)
#
# # 6. Сохраняем итоговый результат
# final_result.show()
# print("Универсальная обработка завершена!")
