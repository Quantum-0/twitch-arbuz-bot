# Архитектура сервиса

> Справочник по устройству приложения. При изменении архитектуры (новые компоненты,
> джобы, потоки данных) — обнови этот файл в том же коммите.

## Компоненты

- **Приложение** (этот репозиторий) — FastAPI, однопроцессный uvicorn (без `--workers`;
  иначе live-метрики `/flowviz` станут per-worker).
- **PostgreSQL** — asyncpg + SQLAlchemy 2.x async, миграции Alembic.
- **Redis** — универсальный кэш (`services.cache.Cache`, префикс `cache:`), стейт
  чат-бота (`services.redis_state_manager.StateManager`, ключи `sm:*`), бакеты
  статистики (`statistics:bucket:*`), кулдауны/дедупы. Два клиента: обычный и binary.
- **MQTT/EMQX** (`services.mqtt.MQTTClient`, aiomqtt) — развязка компонентов:
  вебхуки EventSub публикуются в топики, обработчики подписаны; через топики
  `telegram/*` общение с TG-микросервисом.
- **twibot-tg** (соседний репозиторий `../twibot-tg`, aiogram 3, сервер vds-ams) —
  единственный, кто ходит в Telegram Bot API; прямой доступ к Telegram из этого
  сервиса отсутствует.
- **S3** (`services.s3.FileStorage`, aioboto3) — файлы стикеров/референсов, раздача
  через `/files/{dir}/{file_id}`.
- **Twitch** — Helix API (клиент бота из env-токенов + per-user токены) и
  EventSub-вебхуки (`POST /api/twitch/*`).
- **Внешние сервисы**: aitunnel (генерация картинок, env `OPENAI_BASE_URL`),
  внешний TTS API, Memealerts, Heat (wss-апстрим), Slovotron (вебхуки).

## Поток сообщений чата

Бот не держит IRC-соединение: Twitch присылает каждое сообщение чата вебхуком
EventSub `channel.chat.message` → `routers/api/twitch_eventsub.py` (валидация,
парсинг) → публикация в MQTT → колбэк → `ChatBot.on_message()` → команды/хендлеры →
ответ через Helix `send_chat_message`. Схема — в `README.md`.

## Жизненный цикл (dependencies.py: lifespan)

1. Обработчик необработанных исключений asyncio (лог + GlitchTip).
2. DI-контейнер (`container.Container`, wiring ~28 модулей роутеров; доступ из
   не-endpoint кода — `container_runtime.get_container()`).
3. Redis fail-fast: `config set notify-keyspace-events Egx` + ping обоих клиентов.
4. Инстанцирование сервисов-синглтонов.
5. Старт сервисов: `twitch.startup()` (Helix-клиент бота, twitchAPI сам рефрешит
   по BOT_REFRESH_TOKEN; обёртка `_api_request` для live-метрики), `chat_bot.startup()`
   (регистрация команд и хендлеров) + `chat_bot.update_bot_channels()` (сверка
   подписок `channel.chat.message` с юзерами `enable_chat_bot`), `ai.startup()`,
   `tts_service.startup()`, фоновый поток `stickers_processor` (rembg).
6. MQTT-подписки: чат-сообщения → `chat_bot.on_message`; redemption →
   `eventsub_service.handle_reward_redemption`; slovotron; `telegram/*`
   (результаты доставок, chat_connected/disconnected).
7. Регистрация APScheduler-джоб (таблица ниже), `scheduler.start()`.
8. Супервизоры на время жизни приложения: `mqtt.lifespan()` (реконнект backoff
   1..60с, деградированный режим без брокера) и `state_manager.lifespan()`
   (keyspace-listener чистит индексы истёкших `sm:*`-ключей).
9. Shutdown: scheduler → tts → stickers_processor → dispose engine → close Redis →
   unwire контейнера.

## APScheduler-джобы

| Джоба | Расписание | Что делает |
|---|---|---|
| `update_memealerts_tokens` | cron */3ч | refresh OAuth-токенов Memealerts |
| `update_twitch_tokens` | cron */6ч | refresh user-токенов Twitch (`TwitchTokenService`) |
| `poll_clips` | */5мин | новые клипы/хайлайты → Telegram |
| `reconcile_stream_subs` | час | сверка EventSub stream.online/offline |
| `flush_statistics` | */10мин | Redis-бакеты → таблица `statistics` |
| `cleanup_old_statistics` | 04:00 UTC | ретеншен ~90 дней |
| `server_metrics_tick` / `_flush` / `cleanup_...` | */5мин, */6ч, 04:10 | метрики хоста (см. monitoring.md) |
| `snapshot_sse` | каждую минуту | gauge числа SSE-подключений |
| `flush_live_metrics` | каждые 10с | live-счётчики `/flowviz` → статистика |
| `cleanup_overlay_eventsub` | */3мин | отписка overlay-EventSub без heartbeat/SSE-клиентов |

JobStore — SQLAlchemy: джобы переживают рестарт.

## main.py

Sentry (DSN), OpenTelemetry (OTLP → Jaeger, инструментация FastAPI + httpx,
пропагаторы W3C+B3), Prometheus `/metrics` (без /sse и /static), SessionMiddleware
(cookie-сессии), ASGI-мидлвари (no-cache ES-модулей; HTTP-счётчик `/flowviz`).
Exception handlers: `UserBannedError` → редирект `/banned` (HTML) или 403 (API);
необработанные → 500-страница + Sentry; RequestValidationError → Sentry с телом.

## Конфигурация

Все настройки — `config.py` (`AppSettings` из env). Ключевые группы: `twitch`
(токены бота, extension), `ai` (base_url — aitunnel), `mqtt`, `redis`, `postgres`,
`s3`, `telegram_login`. Токены юзеров в БД шифруются property-сеттерами
`EncryptedString` (`database/models.py`).

Ключевые файлы: `main.py`, `dependencies.py`, `container.py`, `config.py`,
`twitch/state_manager.py`, `services/redis_state_manager.py`.
