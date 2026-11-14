# Мониторинг: статистика, live-метрики, метрики хоста

> Справочник. При изменении поведения — обнови этот файл в том же коммите.

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
- Unique index `statistics_pk` по `(bucket_ts, type, subtype, channel_id)`
  с `NULLS NOT DISTINCT`.

### Категории метрик

- **Counter** (по умолчанию): `value = sum(count)`. Сообщения, награды, команды.
- **`GAUGE_TYPES`** = `{SSE_CONNECTIONS, ACTIVE_CHANNELS}`: `value = avg(count)`.
  В Redis — `hset` (перезапись), в БД — `ON CONFLICT DO UPDATE set count = EXCLUDED.count`.
- **`TIMING_TYPES`** = `{MESSAGE_PROCESSING_TIME, AI_STICKER_PROCESSING_TIME}`:
  `value = sum(sum_ms) / sum(count)` (avg ms). Пишутся через `inc_timing()`.
- **`SUM_TYPES`** = `{HEAT_PROXY_BYTES}`: `value = sum(sum_ms)` (суммарный объём).
- Расширяемо: добавить значение в `StatsType` + в соответствующее множество + точку инкремента.

### Накопление и дамп

- Счётчики накапливаются в Redis-хэшах `statistics:bucket:<ISO-UTC>` (TTL 2ч),
  инкремент — `hincrby`.
- `inc()` / `inc_timing()` / `set_gauge()` / `mark_channel()` — **синхронные**,
  fire-and-forget (через `asyncio.create_task`), вызываются без `await`.
- `flush_to_db()` — APScheduler-джоба раз в 10 мин: SCAN `statistics:bucket:*`,
  `HGETALL`, батч `INSERT ... ON CONFLICT DO UPDATE`. После успешного INSERT ключ
  в Redis удаляется.
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
`_message_processing_task` (id текущей задачи). Проверка
`task_id == id(asyncio.current_task())` гарантирует, что detached-таски
(`IAmBotHandler` 0.5/2.5с задержки, `finish_raffle` 60с) **не записывают**
завышенное время — у них другой `current_task()`.

### API

- `GET /api/user/stats` — single-line график. Query: `type`, `subtype`, `period`, `from`/`to` (UTC).
- `GET /api/user/stats/series` — multi-line (топ-N подтипов). Query: `type`, `period`, `top`, `from`/`to`.
- `GET /api/user/stats/users-count` — кумулятивный график пользователей
  (из `User.created_at`, не из `statistics`).
- Параметр `channel` удалён (per-channel статистика не собирается).

### Фронт `/stats`

Chart.js (CDN v4). Селекторы type/subtype/period/dates + preset-диапазоны (1ч–7д) +
auto-refresh (раз в 10 мин). Фильтры синхронизируются с URL (shareable links).
Для `command_handled`/`sse_connections`/`ai_sticker_processing_time` доступен режим
«раздельно» (по командам / по каналам / по этапам — top-N подтипов на одном графике).
Для gauge-метрик (`sse_connections`, `active_channels`) «(все)» убран — выбирается
конкретный подтип (`total`/`incoming`/...).

## Live-метрики и карта потоков (`/flowviz`)

Живая схема трафика: частицы между узлами (Twitch, бот, PostgreSQL, Redis, MQTT,
twibot-tg, Telegram, Heat, пользователи) летят по кривым Безье с частотой,
пропорциональной реальным скоростям (шапка — суммарные метрики, у рёбер — подписи
с настоящими числами). PoC-исходник движка: `poc/architecture-viz.html`.

- **Страница** `GET /flowviz` (`routers/web/pages.py:flowviz_page`, `staff_auth`),
  движок `static/js/flowviz.js` (canvas, без зависимостей), шаблон
  `templates/flowviz.html` — standalone тёмная страница, НЕ наследует `base.html`.
- **API** `GET /api/admin/flowviz` (`routers/api/admin_api.py:flowviz_rates`,
  `staff_auth`): `{ts, rates: {type: {subtype: rate/с}}, sse: {...}, active_channels}`;
  фронт поллит раз в 1 с, при обрыве — индикатор «нет связи» и затухание потоков.
- **LiveRegistry** (`services/live_metrics.py`) — модульный синглтон кумулятивных
  счётчиков процесса. `bump()` синхронный (dict-инкремент, без `create_task`) —
  можно звать из sync-колбэков (SQLAlchemy-ивенты, `httpx.send`); `rates()` — дифф
  сэмплов за окно ~2 с (лениво сэмплит при каждом вызове). **Валидно только пока
  приложение однопроцессное** (uvicorn без `--workers`) — иначе числа per-worker.
- **Интеграция со статистикой:** `StatisticsService.inc()` дублирует инкремент
  в реестр (`bump_persisted` — не попадает в pending); высокочастотные метрики
  (SQL/Redis/SSE/Twitch API) пишут только `bump()`; джоба `flush_live_metrics`
  (каждые 10 с, dependencies.py) переносит дельты в Redis-бакеты через
  `StatisticsService.flush_live_delta` → таблица `statistics` → графики `/stats`.
  `count_active_channels()` — live-SCARD SET'а активных каналов текущего бакета.
- **Новые StatsType** (`schemas/api.py`): `http_requests`, `db_queries`, `redis_ops`,
  `mqtt_publish`, `mqtt_messages`, `eventsub_events`, `sse_events`,
  `twitch_api_calls` — все обычные counter'ы.
- **Точки инкремента** (каждая — единственная воронка):
  - HTTP — pure-ASGI `HttpRequestCounterMiddleware` (`main.py`), subtype = первый
    сегмент пути; `/static` и `/metrics` исключены;
  - SQL — engine event `before_cursor_execute` на `async_engine.sync_engine`
    (`database/database.py`, `install_db_query_counter`), subtype = глагол
    (SELECT/…); SQL jobstore'а APScheduler идёт через собственный engine и не
    считается (осознанно);
  - Redis — `CountingRedis` + `CountingPipeline` (`services/redis_state_manager.py`):
    фабрика `init_redis` возвращает субкласс; pipeline-команды буферизуются мимо
    `execute_command`, поэтому Pipeline субклассится отдельно;
  - MQTT — `MQTTClient.publish` (на входе, включая дропнутые) и цикл `__listen`
    (`services/mqtt.py`), subtype = первый уровень топика (`telegram`/…).
    Подписка — с `noLocal=True` (MQTT v5): собственные публикации не возвращаются
    эхом и не попадают в `mqtt_messages`;
  - EventSub — `routers/helpers.py:parse_eventsub_payload`, subtype =
    `Twitch-Eventsub-Subscription-Type` (считаются все доставки, включая невалидные);
  - SSE — `SSEManager.broadcast` после проверки «0 клиентов» (только доставленные),
    subtype = канал;
  - Twitch API — обёртка приватного `_api_request` twitchAPI в `Twitch.startup()`
    (хрупкое место: при апгрейде библиотеки метода может не быть — тогда warning
    и метрика отключается) + `install_httpx_twitch_counter()` в `main.py`
    (патчит `httpx.AsyncClient.send` для хостов api/id.twitch.tv; вызывается
    ПОСЛЕ OTel-инструментации httpx).
- **Честные ограничения (v1):** ребро users→Twitch показывает наблюдаемый ботом
  чат (`message_incoming`), users→Heat — наблюдаемый Heat-трафик, а не весь Twitch;
  twitch→бот — все доставки `eventsub_events` (чат идёт через EventSub
  `channel.chat.message`, IRC-путь отключён — `message_incoming` на этом ребре
  считался бы дважды); twibot-tg→Telegram = скорость наших
  `mqtt_publish(telegram/*)` (Bot API зовёт соседний сервис, собственной
  экспорт-статистики у него нет). Эффект наблюдателя не исключён: открытая
  страница /flowviz сама создаёт ~1 HTTP-запрос/с и SCARD/SELECT на поллинг.
- **Тесты:** `tests/unit/test_live_metrics.py`; фронт — страница в каталоге
  `tests/front/pages.py`, мок `/api/admin/flowviz` в `tests/front/api_mocks.py`.

## Метрики хоста (`/server`)

`services/server_metrics.py` (`ServerMetricsService`): метрики хоста без доступа
к файлам хоста (CPU/RAM/load из `/proc` контейнера, диск — statvfs, сеть — veth),
буфер в Redis, агрегация 6-часовых окон, ретеншен 30 дней, алерты. Джобы:
`server_metrics_tick` (*/5мин), `server_metrics_flush` (*/6ч),
`cleanup_server_metrics` (04:10). Страница `/server` (staff) +
`GET /api/user/server-metrics`; healthcheck соседних подсистем —
`services/healthcheck.py` (параллельные дешёвые проверки SELECT 1 / PING / GET,
`GET /api/admin/healthcheck`).

Прод-ошибки — GlitchTip; workflow разбора — в `AGENTS.md`.
