# ИИ-стикеры

> Справочник по фиче. При изменении поведения — обнови этот файл в том же коммите.

Зритель выкупает награду «AI Sticker» (баллы канала) с текстом-промптом → бот
генерирует стикер (PNG с прозрачным фоном), показывает на оверлее, опционально
отправляет в Telegram и в галерею.

## Подключение (панель /panel/ai-stickers)

- `POST /api/user/setup-ai-stickers` — создание награды и подписка EventSub.
- Выбор модели: `gpt-image-1-mini` (дешёвая) / `gpt-image-2.5-flare` /
  `gpt-image-2.5-sunburst` (настройка `ai_sticker_model`).
- **Провайдер — aitunnel** (env `OPENAI_BASE_URL`, `config.py:AISettings.base_url`).
  Решение зафиксировано: переход на routerai невыгоден (нет `gpt-image-2` и
  эндпоинта `/images/edits`, `glm-5.2` дороже на ~22–38%) — провайдера не менять.

## Пайплайн (`services/stickers.py:build_sticker`)

1. Пустой ввод или неподключённый оверлей (SSE `ai-sticker`) → возврат баллов.
2. Кэш: идентичный промпт за последние 7 дней (без @упоминаний) → выдача
   существующей картинки.
3. Баланс стримера: списание с наценкой `cost * ai.cost_multiplier +
   ai.cost_single_call` (см. `docs/balance.md`); при нехватке — `NegativeBalanceError`,
   награда возвращается.
4. Референсы персонажей: `@ники` из промпта ищутся в `CharacterInfo` (только
   approved) → генерация в режиме images.edit с картинками-референсами и/или
   текстовыми описаниями. Гейты:
   - канальный `ai_reference_usage_policy`: deny / with_my_character / allow;
   - персонажный `ai_reference_allow_on_other_channels` (владелец персонажа
     запретил использовать себя на чужих каналах);
   - `ai_require_own_character` — генерация только со своим персонажем.
5. Safety-блок провайдера → возврат баллов; копирайт-эвристика (`_IP_MARKERS`:
   шрек/дисней/покемон/…) → отдельное сообщение про авторские права.
6. Пост-обработка: белая обводка/тень по альфа-каналу (для 2.5-моделей),
   resize (`services/stickers_processor.py` — фоновый поток, onnx/rembg лениво).
7. PNG в S3 (`services/s3.py`) + метаданные в `GeneratedImage` → broadcast
   `sticker_file_id` в SSE `ai-sticker` → показ на оверлее `/overlay/ai-sticker`.
   `get_unshown`/`get_sticker` — выдача неотображённых (после перезагрузки оверлея).

## Референсы персонажей (`CharacterInfo`)

- Загружает сам стример: PNG (до 10 МБ) и/или текстовое описание персонажа
  (`POST /api/user/reference`). Доступно при ≥20 фолловеров (или админ).
- До аппрува модератором в генерации не участвует. Модерация:
  `utils/reference_moderation.py` (захардкоженный список модераторов + админ),
  API `PATCH /api/moderation/references/{id}`, страница `/moderation/references`
  (см. также `routers/api/reference_moderation.py`).

## Доставка в Telegram

При `stickers_enabled` + `stickers_chat_id` стикер уходит фото или документом
в TG-чат через MQTT (см. `docs/telegram.md`).

## Галереи и приватность

- `/stickers` — галерея стикеров (режимы: мои / «где я» / «от меня»),
  `/references` — галерея approved-референсов. Только залогиненные.
- Показ стикеров/референса в профиле — настройки приватности юзера.

Ключевые файлы: `services/stickers.py`, `services/ai.py`,
`services/stickers_processor.py`, `routers/api/user/` (setup/recent/reference),
`templates/ai_stickers.html`, `static/js/` панели.
