# AGENTS.md

Краткая шпаргалка для агентов, работающих с этим репозиторием.

## Запреты

- **ЗАПРЕЩЕНО** делать git commit / push без явной просьбы пользователя.
- **ЗАПРЕЩЕНО** подключаться к серверу (ssh `vds-msk` и др.) без явной просьбы пользователя.
  Любые действия на сервере (git pull, docker compose, просмотр логов, миграции БД)
  выполняются только после прямого разрешения пользователя.

## Технологический стек

- **Язык:** Python 3.11
- **Бэкенд:** FastAPI 0.116
- **БД:** PostgreSQL (asyncpg + SQLAlchemy 2.x async)
- **Миграции:** Alembic
- **Кэш / стейт:** Redis (`services.cache.Cache`, `services.redis_state_manager.StateManager`)
- **Pub-Sub:** MQTT/EMQX (`services/mqtt.MQTTClient`, `aiomqtt`)
- **Трассировка:** Sentry + OpenTelemetry (`tracer`)
- **DI:** `dependency_injector` (контейнер `container.Container`, wiring в `container.py`)
- **Шаблоны:** Jinja2 (`templates/`)
- **Фронтенд:** Vanilla JS + CSS, статика в `static/`
- **Планировщик:** APScheduler (`container.scheduler`)
- **Менеджер зависимостей:** Poetry

## Команды для разработки

```bash
poetry run ruff check .                # линтер
poetry run ruff check --fix .          # безопасные фиксы
poetry run ruff format .               # форматирование
poetry run mypy <путь_к_файлу>          # проверка типов
poetry run alembic revision --autogenerate -m "title"  # создать миграцию
poetry run alembic upgrade head        # применить миграции
poetry run uvicorn main:app --reload   # запустить дев-сервер
```

**Перед завершением задачи обязательно:** `poetry run ruff check <файлы>` + `poetry run mypy <файлы>`.

## Соглашения по коду

- Длина строки: 120 символов.
- FastAPI-роуты с DI: `Annotated[T, Depends(Provide[Container.x])]` + `@inject`.
- Не-endpoint функции берут зависимости через `from container_runtime import get_container`.
- Новый модуль с роутами → добавить в `container.Container.wiring_config.modules`.

## Структура проекта

```
main.py                  # FastAPI entry point, роутеры, lifespan
container.py             # DI-контейнер и wiring_config
dependencies.py          # get_db, lifespan, get_container
database/models.py       # SQLAlchemy-модели
routers/
    routers.py           # сборка api_router и user_router
    web/                 # HTML-страницы и оверлеи
    api/                 # JSON-API (user_api, extension, eventsub, slovotron, user/*)
services/                # бизнес-логика (cache, mqtt, twitch, statistics, sse_manager, ...)
twitch/chat/             # чат-бот (bot.py, command_manager.py, commands/, handlers/)
schemas/api.py           # Pydantic-схемы (StatsType, StatsPeriod, ...)
utils/                   # хелперы (streamers, streamers_sort, overlays, enums)
templates/               # Jinja2-шаблоны
static/                  # CSS, JS (streamers.js, stats.js, heat-ws.js)
```

## Миграции БД

Миграции создаются ТОЛЬКО через `alembic revision --autogenerate -m "title"`.
После генерации отредактируй файл: оставь в `upgrade()`/`downgrade()` только
изменения, связанные с твоей задачей.

## /streamers — фильтры и сортировка

- HTML `/streamers` отдаёт заглушку; список грузится через `fetch('/api/user/streamers?...')` (`static/js/streamers.js`).
- API: `sort` (recommended|followers|created|name), `order` (asc|desc), фильтры `f_bot`/`f_meme`/`f_ai`/`f_overlay`/`f_online`/`f_pants`.
- Логика: `utils/streamers.py:get_streamers_list`. Скоринг: `utils/streamers_sort.py:compute_streamer_score`.
- `User.overlays_last_usage` обновляется в `utils/overlays.py:touch_overlay_usage` (не чаще 1/час). «Использует оверлеи» = обращение не позднее 14 дней.

## SSE — Server-Sent Events

Бот отдаёт стримерам данные для оверлеев OBS через SSE (один HTTP-соединение
на клиент, сервер пушит события в `text/event-stream`).

- **Роут:** `GET /sse/{user_id}/{channel}` в `routers/sse.py`. `user_id` — Twitch channel ID стримера.
- **Аутентификации нет** (кроме `slovotron` — требует `?secret=<UUIDv3>`).
- **Каналы** (`utils/enums.py:SSEChannel`): `ai-sticker`, `heat`, `slovotron`, `msg` (последний не используется).
- **`SSEManager`** (`services/sse_manager.py`): хранит активные подключения в памяти,
  `broadcast()` → `put_nowait` во все очереди канала (неблокирующе).
  Grace-период 15с после дисконнекта (чтобы микро-разрывы EventSource не отменяли награды).
  `snapshot()` — мгновенный срез для метрики `sse_connections`.
- **Heat upstream** (`services/heat_upstream.py`): единственный канал с upstream-WS
  (`wss://heat-api.j38.net/...`). Один `HeatUpstreamConnection` на user_id, лениво
  создаётся при подключении SSE-клиента на канал `HEAT`, глушится при отключении.

## Подсистема статистики (`/stats`)

Метрики агрегируются по 10-минутным бакетам. Модель `Statistics`
(`database/models.py`), сервис `StatisticsService` (`services/statistics.py`),
API `/api/user/stats`, фронт `/stats` (`templates/stats.html` + `static/js/stats.js`).

### Таблица `statistics`

- `bucket_ts` (timestamptz) — начало 10-минутного бакета (UTC).
- `type` (varchar64) — тип метрики (см. `StatsType` в `schemas/api.py`).
- `subtype` (varchar64, default `""`) — для `reward_*`: `received`/`succeed`/`failed`/...;
  для `command_handled` — имя команды; для `active_channels` — `incoming`/`outgoing`;
  для `sse_connections` — `total`/`unique_users`/`unique_pairs`/`<channel_name>`
  (всего / по пользователям / по типам / по каналам в UI).
- `channel_id` (bigint, NULL) — **всегда NULL**: per-channel статистика не собирается.
- `count` (int) — счётчик или gauge-значение.
- `sum_ms` (bigint, nullable) — для timing/sum-метрик.
- Unique index `statistics_pk` по `(bucket_ts, type, subtype, channel_id)` с `NULLS NOT DISTINCT`.

### Категории метрик

- **Counter** (по умолчанию): `value = sum(count)`. Сообщения, награды, команды.
- **`GAUGE_TYPES`** = `{SSE_CONNECTIONS, ACTIVE_CHANNELS}`: `value = avg(count)`.
  В Redis — `hset` (перезапись), в БД — `ON CONFLICT DO UPDATE set count = EXCLUDED.count`.
- **`TIMING_TYPES`** = `{MESSAGE_PROCESSING_TIME, AI_STICKER_PROCESSING_TIME}`:
  `value = sum(sum_ms) / sum(count)` (avg ms). Пишутся через `inc_timing()`.
- **`SUM_TYPES`** = `{HEAT_PROXY_BYTES}`: `value = sum(sum_ms)` (суммарный объём).
- Расширяемо: добавить значение в `StatsType` + в соответствующее множество + точку инкремента.

### Накопление и дамп

- Счётчики накапливаются в Redis-хэшах `statistics:bucket:<ISO-UTC>` (TTL 2ч), инкремент — `hincrby`.
- `inc()` / `inc_timing()` / `set_gauge()` / `mark_channel()` — **синхронные**, fire-and-forget
  (через `asyncio.create_task`), вызываются без `await`.
- `flush_to_db()` — APScheduler-джоб раз в 10 мин: SCAN `statistics:bucket:*`, `HGETALL`,
  батч `INSERT ... ON CONFLICT DO UPDATE`. После успешного INSERT ключ в Redis удаляется.
- `cleanup_old_data()` — раз в сутки (04:00 UTC), удаляет строки старше 90 дней.
- `SUM_MS_SUFFIX = "__sum_ms"` (без двоеточия!) — суффикс для sum_ms-полей в Redis,
  чтобы `_parse_field` не разбил их в отдельный subtype.

### Точки инкремента

- `twitch/chat/bot.py:on_message` → `inc("message_incoming")` + `mark_channel("incoming")`.
- `twitch/chat/bot.py:send_message` → `inc("message_outgoing")` + `mark_channel("outgoing")`
  + `inc_timing("message_processing_time")` (при первом ответе в задаче).
- `twitch/chat/command_manager.py:handle` → `inc("command_handled", subtype=cmd.command_name)`.
- `services/eventsub_service.py` → `inc("reward_memecoins"/"reward_ai_stickers", subtype=...)`.
- `services/heat_upstream.py:_run` → `inc("heat_proxy_messages")` + `inc_timing("heat_proxy_bytes")`.
- `dependencies.py:snapshot_sse_job` (раз в минуту) → `set_gauge("sse_connections", snapshot)`.
- `services/ai.py` + `services/stickers.py` → `inc_timing("ai_sticker_processing_time", subtype=...)`.

### Метрика `message_processing_time`

Замеряет время от `on_message` до **первого** `send_message` в **той же задаче**.
Использует два ContextVar: `_message_processing_start` (monotonic) и
`_message_processing_task` (id текущей задачи). Проверка `task_id == id(asyncio.current_task())`
гарантирует, что detached-таски (`IAmBotHandler` 0.5/2.5с задержки, `finish_raffle` 60с)
**не записывают** завышенное время — у них другой `current_task()`.

### API

- `GET /api/user/stats` — single-line график. Query: `type`, `subtype`, `period`, `from`/`to` (UTC).
- `GET /api/user/stats/series` — multi-line (топ-N подтипов). Query: `type`, `period`, `top`, `from`/`to`.
- `GET /api/user/stats/users-count` — кумулятивный график пользователей (из `User.created_at`, не из `statistics`).
- Параметр `channel` удалён (per-channel статистика не собирается).

### Фронт `/stats`

Chart.js (CDN v4). Селекторы type/subtype/period/dates + preset-диапазоны (1ч–7д) +
auto-refresh (раз в 10 мин). Фильтры синхронизируются с URL (shareable links).
Для `command_handled`/`sse_connections`/`ai_sticker_processing_time` доступен режим «раздельно»
(по командам / по каналам / по этапам — top-N подтипов на одном графике).
Для gauge-метрик (`sse_connections`, `active_channels`) «(все)» убран —
выбирается конкретный подтип (`total`/`incoming`/...).

## TODO

Планы по развитию (followers_count refresh, activity tables, twitch OAuth token refresh)
см. в `TODO.md`.
