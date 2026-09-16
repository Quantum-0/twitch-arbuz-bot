# PROMPT_TELEGRAM_INTEGRATION.md

## Исчерпывающий контекст для продолжения работы над Telegram-интеграцией

---

## 1. Что мы строим

Telegram-интеграцию для Twitch-бота Quantum0's Bot. Два микросервиса:

- **twitch-bot** (основной сервис, RU-сервер, `~/PycharmProjects/twitch-bot`) — FastAPI + SQLAlchemy + APScheduler + MQTT-клиент.
- **twibot-tg** (TG-микросервис, Amsterdam-сервер, `~/PycharmProjects/twibot-tg`) — aiogram 3.x + aiomqtt + FastAPI (для debug API).

Связь: **MQTT** (EMQX, TLS порт 8883, prefix `twibot`, protocol v5, QoS 0) — основной канал. HTTP API в twibot-tg — только для debug и для `POST /api/connect`.

TG-бот: `@quantum0s_twitch_bot`, токен в twibot-tg `.env`.

### Функции интеграции

Для каждого стримера можно подключить **3 отдельных Telegram-чата** (или использовать один общий):

1. **Stream notifications** — уведомления о начале/окончании стрима.
2. **Clips** — автозагрузка новых клипов Twitch в Telegram (как видео или ссылкой).
3. **AI Stickers** — автозагрузка AI-сгенерированных стикеров в Telegram.

---

## 2. Архитектура

### Модель `TelegramSettings` (БД, `database/models.py`)

1:1 с `User`, lazy-создание (нет строки, пока юзер не начнёт настраивать). `user_id` — PK (без суррогатного id).

Поля:
- `stream_chat_id`, `stream_chat_type`, `stream_connected_at` — чат для уведомлений о стриме
- `clips_chat_id`, `clips_chat_type`, `clips_connected_at` — чат для клипов
- `stickers_chat_id`, `stickers_chat_type`, `stickers_connected_at` — чат для стикеров
- `stream_notification_enabled` (bool) — включить уведомления о начале стрима
- `stream_offline_behavior` (`keep` | `delete` | `message`) — поведение при окончании стрима
- `last_stream_message_id` (str | None) — ID сообщения о начале стрима в TG (для удаления/редактирования)
- `clips_enabled` (bool) — автозагрузка клипов
- `clips_mode` (`all` | `featured`) — все клипы или только избранные (Twitch `is_featured`)
- `clips_delivery` (`link` | `video`) — отправлять ссылку или видео-файл
- `last_clip_date` (datetime | None) — дата последнего отправленного клипа (дедупликация)
- `stickers_enabled` (bool) — автозагрузка стикеров
- `stickers_mode` (`photo` | `document`) — способ отправки (sendPhoto / sendDocument)
- `twitch_to_tg_enabled`, `tg_to_twitch_enabled` (bool) — mirroring (не реализуем в MVP)
- `telegram_user_id` (str | None) — для mirroring (не реализуем в MVP)

Relationship: `User.telegram` → `TelegramSettings | None` (lazy). `joinedload(User.telegram)` добавлен в `user_auth` и `user_auth_optional` (`routers/security_helpers.py`).

### Подключение чата (connection flow)

1. Фронтенд (панель) → `POST /api/user/telegram/connect` `{scope, chat_type}`.
2. Основной сервис вызывает `POST {telegram_service_url}/api/connect` (httpx, `X-Api-Key` header) → TG-сервис создаёт `PendingConnection` в `PendingStore` (in-memory, 30-мин TTL), возвращает `short_id` (8 hex chars).
3. Основной сервис возвращает `{url: "https://t.me/{bot_username}?start={short_id}"}`.
4. Фронтенд делает `window.open(url, '_blank')`.
5. Юзер в Telegram: `/start <short_id>` → бот находит pending, показывает inline кнопку "Добавить в канал/группу" с URL `?startchannel&admin=post_messages+delete_messages` (для channel) или `?startgroup&admin=post_messages` (для group).
6. Юзер добавляет бота → `my_chat_member` update → бот сопоставляет `tg_user_id → short_id` (in-memory map), находит pending, публикует MQTT `twibot/telegram/chat_connected` `{user_id, scope, chat_id, chat_type, chat_title}`.
7. Основной сервис: `handle_chat_connected()` (`services/telegram_integration.py`) сохраняет `chat_id`/`chat_type` в `TelegramSettings` по scope.

### MQTT topics

- `twibot/telegram/chat_connected` — TG-сервис → основной (привязка чата)
- `twibot/telegram/send_message` — основной → TG-сервис (отправка текста)
- `twibot/telegram/send_photo` — основной → TG-сервис (отправка фото)
- `twibot/telegram/send_video` — основной → TG-сервис (отправка видео)
- `twibot/telegram/send_document` — основной → TG-сервис (отправка документа)
- `twibot/telegram/delete_message` — основной → TG-сервис (удаление сообщения)
- `twibot/telegram/result` — TG-сервис → основной (результат отправки, `request_id` для корреляции)

TG-сервис подписан на `twibot/telegram/+` (все subtopics).

### Pydantic-схемы (`schemas/telegram.py`)

- `TelegramSettingsSchema` — GET /settings
- `TelegramSettingsUpdateSchema` — POST /settings (partial update)
- `TelegramConnectSchema` — POST /connect
- `SendMessageRequest`, `SendPhotoRequest`, `SendVideoRequest`, `SendDocumentRequest`, `DeleteMessageRequest` — MQTT-контракты
- `SendResult` — ответ TG-сервиса

---

## 3. Что уже реализовано

### Основной сервис (twitch-bot)

- **`services/twitch_token_service.py`** — `TwitchTokenService` с `get_valid_access_token()`, `run_periodic_update()`, Redis-лок, ретраи, инвалидация при `invalid_grant`. Зарегистрирован в `container.py`. APScheduler job `update_twitch_tokens` (каждые 6ч, minute=45).
- **`routers/web/service_routes.py`** — OAuth callback сохраняет `twitch_token_expires_at`.
- **`database/models.py`** — `TelegramSettings` model + `User.twitch_token_expires_at` + relationship `User.telegram`.
- **`utils/telegram.py`** — `get_telegram_settings()` (defaults) + `ensure_telegram_settings()` (lazy create).
- **`routers/api/user/telegram.py`** — `GET/POST /api/user/telegram/settings`, `POST /api/user/telegram/connect` (через httpx → TG-сервис), `POST /api/user/telegram/disconnect`. Wired в `user_api.py` и `container.py`.
- **`schemas/telegram.py`** — все схемы.
- **`templates/panel/telegram.html`** — фронтенд панели. Обёрнут в `{% if user.login_name == "quantum075" %}` (видно только owner). Показывает кнопки подключения для неподключенных чатов, тоглы и селекты для настроек. `window.open` вместо `window.location.href`.
- **`services/telegram_integration.py`** — `handle_chat_connected()` сохраняет привязку чата.
- **`services/clips_poller.py`** — `ClipsPollerService` (полная реализация, см. ниже).
- **`container.py`** — `ClipsPollerService` зарегистрирован.
- **`dependencies.py`** — MQTT subscription для `telegram/chat_connected`, APScheduler job `poll_clips` (раз в 5 мин, second=30).
- **`static/js/panel-scripts.js`** — `initToggles()` исключает `id^="tg-"` (Telegram-тоглы управляются отдельным `telegramSaveSettings()`).
- **Миграции**: `f7f7298843e9` (twitch_token_expires_at + telegram_settings table), `3c9ce04fdc36` (clips_delivery column). Обе применены.

#### ClipsPollerService (детально)

- APScheduler job раз в 5 минут.
- SELECT пользователей с `clips_enabled=True`, `clips_chat_id` задан, `_access_token` есть (используем `User._access_token` — реальная колонка, т.к. `access_token` это property с decrypt).
- Для каждого: `TwitchTokenService.get_valid_access_token()` → `GET /helix/clips?broadcaster_id=<twitch_id>&started_at=<last_clip_date>&first=100` (пагинация до 10 страниц).
- **Дедупликация**: Twitch `started_at` инклюзивный, поэтому после получения фильтруем клипы с `created_at <= last_clip_date`.
- Фильтр `clips_mode`: `featured` → только `is_featured=True`.
- Отправка:
  - `clips_delivery == "link"` → MQTT `telegram/send_message` со ссылкой.
  - `clips_delivery == "video"` → `GET /helix/clips/download?id=<clip_id>` для получения прямого MP4 URL → MQTT `telegram/send_video`. Fallback на ссылку при ошибке.
- `last_clip_date` обновляется на `max(clip.created_at)` после отправки. При первом запуске — `NOW() - 1s`.
- Safety: lookback не дальше 7 дней.

### TG-сервис (twibot-tg)

- **`main.py`** — FastAPI + aiogram Dispatcher + polling (`handle_signals=False`, `drop_pending_updates=True`, `allowed_updates=[MESSAGE, MY_CHAT_MEMBER]`).
- **`bot_handlers.py`** — `PendingStore`, `BotHandlers` (`/start <short_id>`, `my_chat_member`). In-memory `tg_user_id → short_id` map.
- **`handlers.py`** — `TelegramHandlers` (MQTT → Telegram Bot API): send_message, send_photo, send_video, send_document, delete_message. Публикует результат в `telegram/result`.
- **`mqtt_client.py`** — MQTT-клиент с авто-переподключением, подписка на `twibot/telegram/+`.
- **`api.py`** — `POST /api/connect` (создаёт pending, возвращает short_id, всегда доступен) + debug endpoints (send_message/photo/video/document/delete, защищены `api_debug_enabled`).
- **`schemas.py`** — Pydantic-схемы (дублируют `schemas/telegram.py` из основного сервиса).
- **`config.py`** — `tg_bot_token`, `mqtt_*`, `api_key`, `api_debug_enabled`, `main_service_url`.
- **`Dockerfile`**, **`docker-compose.yml`**, **`pyproject.toml`** (aiogram 3.x, aiomqtt, pyjwt, fastapi, httpx, uvicorn).

---

## 4. Что осталось реализовать

### 4.1. Уведомления о начале/окончании стрима

**Текущее состояние**: В `TelegramSettings` есть поля `stream_notification_enabled`, `stream_offline_behavior` (`keep`/`delete`/`message`), `last_stream_message_id`. В UI есть тоглы и селект. Но **нет логики отправки**.

**Что нужно сделать**:

Основной сервис уже получает EventSub-ивенты через `TwitchEventSubService` (`services/eventsub_service.py`). Но сейчас подписки только на `channel.chat`, `channel.channel_points_custom_reward_redemption`, `channel.raid`. Нужно подписаться на `stream.online` и `stream.offline`.

**Вариант 1 (через EventSub)**: Подписаться на `stream.online` и `stream.offline` для каждого пользователя с `stream_notification_enabled=True`. Обработка в `TwitchEventSubService`: при `stream.online` → MQTT `telegram/send_message` в `stream_chat_id` с текстом "Стрим начался!", сохранить `message_id` в `last_stream_message_id`. При `stream.offline` → поведение по `stream_offline_behavior`: `delete` → MQTT `telegram/delete_message` с `last_stream_message_id`; `message` → MQTT `telegram/send_message` "Стрим завершён"; `keep` → ничего.

**Вариант 2 (polling)**: Добавить APScheduler job, которая периодически проверяет `GET /helix/streams?user_id=<twitch_id>` для каждого пользователя с `stream_notification_enabled`.

Рекомендуется **Вариант 1** (EventSub), т.к. инфраструктура уже есть. Но нужно изучить как именно создаются EventSub-подписки в текущем коде (`twitch/client/twitch.py` — методы подписки) и добавить создание подписок при включении `stream_notification_enabled` (и отписку при выключении).

**Точки интеграции**:
- `services/eventsub_service.py` — добавить handlers для stream.online/offline.
- `routers/api/user/telegram.py` `update_telegram_settings()` — при включении `stream_notification_enabled` создавать EventSub-подписку; при выключении — удалять.
- `services/telegram_integration.py` — или отдельный handler для stream events → MQTT publish.
- Нужно получить валидный Twitch user token (`TwitchTokenService`) для создания подписки.

**Шаблон сообщения о начале стрима**: "🔴 {channel_name} начал стрим! Заголовок: {title}\n{stream_url}"
**Шаблон сообщения об окончании**: "⚪️ Стрим завершён."

### 4.2. Отправка AI-стикеров в Telegram

**Текущее состояние**: В `TelegramSettings` есть `stickers_enabled` и `stickers_mode` (`photo` | `document`). В UI есть тоглы и селект. Но **нет логики отправки**.

**Что нужно сделать**:

Стикеры генерируются в `StickersService.build_sticker()` (`services/stickers.py:340`) → сохраняются в S3 (`FileStorageDir.AI_GENERATED_STICKER/{file_id}.png`) → отдаются через роут `GET /files/{dir}/{file_id}` (`routers/web/file_storage.py`).

Вызов происходит в `TwitchEventSubService.reward_ai_sticker()` (`services/eventsub_service.py:314`): после генерации стикера, он транслируется через SSE (`_ssem.broadcast`) на оверлей OBS.

**Нужно добавить**: после `build_sticker()` и broadcast, если у юзера `stickers_enabled=True` и `stickers_chat_id` задан — отправить стикер в Telegram через MQTT.

**Проблема**: TG-сервис `send_photo`/`send_document` принимает URL (`photo_url`/`document_url`). Стикер доступен по URL `https://bot.quantum0.ru/files/{AI_GENERATED_STICKER}/{file_id}` — это публичный URL, TG-сервис может его скачать.

Но! Роут `GET /files/{dir}/{file_id}` (`routers/web/file_storage.py`) — без аутентификации, отдаёт `image/png`. TG-сервис должен иметь возможность скачать по этому URL. `main_service_url` в twibot-tg config = `https://bot.quantum0.ru`.

**Точки интеграции**:
- `services/eventsub_service.py:reward_ai_sticker()` — после `self._ssem.broadcast(...)` добавить:
  ```python
  if user.telegram and user.telegram.stickers_enabled and user.telegram.stickers_chat_id:
      sticker_url = f"{settings.base_url}/files/{FileStorageDir.AI_GENERATED_STICKER}/{sticker_id}"
      # MQTT publish telegram/send_photo или telegram/send_document
  ```
- Нужно добавить `base_url` или использовать существующий URL для генерации публичных ссылок. Проверить config.py на наличие `base_url`.
- Нужно передать `mqtt` (MQTTClient) в `TwitchEventSubService` — сейчас он не передаётся в `container.py`.
- `selectinload(User.telegram)` нужно добавить в `_get_user_by_id_or_login()` в `eventsub_service.py` (сейчас загружаются `settings`, `memealerts`, `links`, `tts`, но не `telegram`).

### 4.3. Починить скачивание видео клипов

**Текущее состояние**: `clips_delivery == "video"` → `ClipsPollerService._fetch_clip_download_url()` вызывает `GET https://api.twitch.tv/helix/clips/download?id=<clip_id>`. Если возвращается URL — отправляем через `telegram/send_video`. При ошибке — fallback на ссылку.

**Проблема**: Клип не скачивается. Нужно проверить:
1. **Twitch Get Clips Download API** (`GET /helix/clips/download?id=<clip_id>`) — это новый endpoint (2025). Проверить, что он действительно существует и доступен. Возможно, он требует special scope или ещё не public.
2. **Альтернатива**: Известный workaround — взять `thumbnail_url` из Get Clips response, заменить `%{width}x%{height}` на `1280x720` — это даёт прямой URL на MP4. Пример: `https://clips-media-assets2.twitch.tv/...mp4`. Но Twitch периодически меняет это.
3. **Логирование**: Добавить логирование ответа от `/helix/clips/download` чтобы понять что возвращает API (status code, body).
4. **Scope**: Для Get Clips достаточно user token с scope `clips:edit` или `channel:manage:clips`. Проверить что `user_scope` в `config.py` включает нужные scope.

**Что проверить в первую очередь**:
- Посмотреть логи twibot-tg при попытке отправки видео — TelegramAPIError может указать на проблему (например, URL не является прямым MP4, или Telegram не может скачать).
- Добавить логирование в `_fetch_clip_download_url()` — response status, body, полученный URL.
- Проверить `user_scope` в `config.py` — включает ли он нужные Twitch scope для clips.

---

## 5. Репозиторий и GitHub

### twibot-tg — НЕ в git!

`~/PycharmProjects/twibot-tg` — **не является git-репозиторием**. Нужно:

1. `git init` в `~/PycharmProjects/twibot-tg`.
2. Создать `.gitignore` (уже есть минимальный, но проверить — должен исключать `.env`, `__pycache__/`, `.ruff_cache/`, `poetry.lock` опционально).
3. Создать репозиторий на GitHub (организация `Quantum-0`, название `twibot-tg`, private).
4. `git add . && git commit -m "Initial commit: Telegram microservice for twibot"`.
5. `git remote add origin git@github.com:Quantum-0/twibot-tg.git`.
6. `git push -u origin main`.

### twitch-bot — изменения с коммита `64ea911`

Все изменения в основном сервисе начинаются с коммита `64ea911` (включительно). Изменённые файлы:

```
.gitignore                                         |   4 +
alembic/versions/3c9ce04fdc36_add_clips_delivery_to_telegram_settings.py |  28 ++
alembic/versions/f7f7298843e9_add_twitch_token_expires_at_and_.py        |  58 ++++
config.py                                          |   4 +
container.py                                       |  10 +
database/models.py                                 |  67 +++++
dependencies.py                                    |  29 ++
routers/api/user/telegram.py                       | 135 +++++++++
routers/api/user_api.py                            |   2 +
routers/security_helpers.py                        |   2 +
routers/web/service_routes.py                      |   7 +
schemas/telegram.py                                | 115 ++++++++
services/clips_poller.py                           | 297 +++++++++++++++++++
services/telegram_integration.py                   |  71 +++++
services/twitch_token_service.py                   | 319 +++++++++++++++++++++
static/js/panel-scripts.js                         |   2 +-
templates/panel.html                               |   4 +
templates/panel/telegram.html                      | 214 ++++++++++++++
twitch/client/twitch.py                            |   5 +-
utils/telegram.py                                  |  70 +++++
```

Коммиты: `64ea911`, `5187902`, `c531f90`, `be2fd67`, `39c5470`, `a15cb2f`.

**Review**: Нужно сделать ревью всех изменений с `64ea911` по `HEAD` (a15cb2f). `git diff 64ea911^..HEAD` — полный diff. Проверить:
- Безопасность (нет утечки секретов, токенов).
- Корректность SQL-запросов (особенно `User._access_token` вместо property).
- Обработка ошибок в MQTT handlers.
- Корректность миграций.
- Соответствие конвенциям проекта (ruff, mypy, 120 chars).
- `jwt` импорт удалён из `routers/api/user/telegram.py` — проверить что `PyJWT` не нужен.

---

## 6. Структура twibot-tg (для агента)

```
twibot-tg/
├── main.py            # FastAPI + aiogram Dispatcher + polling, lifespan
├── bot_handlers.py    # PendingStore, BotHandlers (/start, my_chat_member)
├── handlers.py        # TelegramHandlers (MQTT → Telegram Bot API)
├── mqtt_client.py     # MQTTClient (auto-reconnect, subscribe twibot/telegram/+)
├── api.py             # POST /api/connect (always-on) + debug_router (api_debug_enabled)
├── schemas.py         # Pydantic-схемы (дублируют schemas/telegram.py из twitch-bot)
├── config.py          # Settings (tg_bot_token, mqtt_*, api_key, api_debug_enabled, main_service_url)
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml     # aiogram 3.x, aiomqtt, fastapi, httpx, uvicorn
├── .env               # tg_bot_token, mqtt credentials, api_key
├── .env.example
└── .gitignore
```

---

## 7. Важные детали реализации

### `User._access_token` vs `User.access_token`

`User.access_token` — это `@property` с `decrypt_value()`. В SQLAlchemy-запросах нужно использовать `User._access_token` (реальная колонка `mapped_column("access_token", String)`).

### MQTT publish — async

`mqtt.publish()` — async метод. Все вызовы в `clips_poller.py` используют `await`. В `telegram_integration.py` и `eventsub_service.py` тоже нужно `await`.

### `initToggles()` в panel-scripts.js

`document.querySelectorAll('.toggle-switch:not([data-name^="tts_"]):not([id^="tg-"])')` — Telegram-тоглы исключены из общего обработчика, т.к. они управляются отдельным `telegramSaveSettings()`.

### `telegram_service_url` и `telegram_service_api_key`

В `config.py` основного сервиса:
- `telegram_service_url: str = "http://localhost:8001"` — URL TG-сервиса
- `telegram_service_api_key: str = "changeme"` — API key для `POST /api/connect`

В `config.py` twibot-tg:
- `api_key: str = "changeme"` — тот же ключ
- `main_service_url: str = "https://bot.quantum0.ru"` — URL основного сервиса (для публичных ссылок на стикеры)

### Scope для Twitch user token

Проверить `user_scope` в `config.py` — нужен ли scope для `clips:edit` или `channel:manage:clips` для Get Clips Download API.

### S3 URL для стикеров

Стикеры хранятся в S3 (`FileStorageDir.AI_GENERATED_STICKER/{file_id}.png`). Публичный URL: `https://bot.quantum0.ru/files/{AI_GENERATED_STICKER}/{file_id}` (роут `GET /files/{dir}/{file_id}` в `routers/web/file_storage.py`, без аутентификации). TG-сервис может скачать по этому URL через `send_photo(url=...)`.

### Telegram-панель — только для owner

`templates/panel/telegram.html` обёрнут в `{% if user.login_name == "quantum075" %}`. Это нужно убрать когда фича будет готова для всех.

---

## 8. Команды для разработки

```bash
# Основной сервис
poetry run ruff check <файлы>
poetry run ruff format <файлы>
poetry run mypy <файлы>
poetry run alembic upgrade head
poetry run uvicorn main:app --reload

# TG-сервис (в ~/PycharmProjects/twibot-tg)
poetry run ruff check <файлы>
poetry run uvicorn main:app --reload --port 8001
```

Перед завершением задачи: `poetry run ruff check <файлы>` + `poetry run mypy <файлы>`.

---

## 9. План работ (порядок)

1. **Починить скачивание видео клипов** — добавить логирование в `_fetch_clip_download_url()`, проверить Twitch Get Clips Download API, проверить `user_scope`, попробовать workaround с `thumbnail_url`.
2. **Реализовать уведомления о начале/окончании стрима** — EventSub подписки на `stream.online`/`stream.offline`, handlers в `eventsub_service.py`, MQTT publish в `stream_chat_id`.
3. **Реализовать отправку AI-стикеров** — в `reward_ai_sticker()` после broadcast добавить MQTT publish в `stickers_chat_id`, передать `mqtt` в `TwitchEventSubService`, добавить `selectinload(User.telegram)`.
4. **Завернуть twibot-tg в git и загрузить на GitHub** — `git init`, создать репо, push.
5. **Ревью всех изменений** — `git diff 64ea911^..HEAD`, проверить безопасность, корректность, конвенции.
