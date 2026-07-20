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
- **Кэш / стейт:** Redis (`services.cache.Cache`, `services.redis_state_manager.StateManager`) — cooldown команд, лурки, пирамидки и т.п.
- **Pub-Sub (событий Twitch):** MQTT/EMQX (`services/mqtt.MQTTClient`, `aiomqtt`). Топики: `twitch/<channel>/message`, `twitch/<channel>/reward-redemption`, `twitch/<channel>/raid`, `slovotron/<event>/<channel>`. Webhook'и Twitch EventSub публикуют в MQTT, бот подписывается в `dependencies.py:lifespan`.
- **Трассировка/ошибки:** Sentry + OpenTelemetry (`tracer`).
- **DI:** `dependency_injector` (контейнер `container.Container`, wiring в `container.py`)
- **Шаблоны:** Jinja2 (`templates/`)
- **Фронтенд:** Vanilla JS + CSS (без фреймворков), статика в `static/`
- **Планировщик:** APScheduler (`container.scheduler`)
- **Менеджер зависимостей:** Poetry

## Команды для разработки

Все команды запускать через `poetry run` (это активирует venv из `.venv/`):

```bash
poetry run ruff check .                # линтер (конфиг в pyproject.toml)
poetry run ruff check --fix .          # применить безопасные фиксы
poetry run ruff format .               # форматирование кода
poetry run mypy <путь_к_файлу>          # проверка типов (конфиг в pyproject.toml)
poetry run alembic revision --autogenerate -m "title"  # создать миграцию
poetry run alembic upgrade head        # применить миграции
poetry run uvicorn main:app --reload   # запустить дев-сервер
```

**Перед завершением задачи обязательно запустить:**

```bash
poetry run ruff check <изменённые файлы>
poetry run mypy <изменённые файлы>
```

## Соглашения по коду

- Длина строки: 120 символов.
- Импорты: isort-стиль (ruff `I`).
- FastAPI-роуты с DI используют `Annotated[T, Depends(Provide[Container.x])]` + декоратор `@inject`.
- Если функция используется как обычная (не FastAPI endpoint), а не через роутер, не используйте `Depends(Provide[...])` — вместо этого берите зависимость напрямую через `from container_runtime import get_container` (см. пример `utils/overlays.py:touch_overlay_usage`).
- При добавлении нового модуля с роутами, обязательно добавьте его в `container.Container.wiring_config.modules` — иначе DI не сработает в рантайме и параметры будут приходить как объекты `Provide`.

## Структура проекта

```
main.py                  # точка входа FastAPI, роутеры, lifespan
container.py             # DI-контейнер и wiring_config
dependencies.py          # get_db, lifespan, get_container
database/
    models.py            # SQLAlchemy-модели
    database.py          # async_engine, AsyncSessionLocal
alembic/versions/        # миграции
routers/
    routers.py           # сборка api_router и user_router
    web/                 # HTML-страницы и роуты оверлеев
        pages.py         # /, /streamers, /profile/<name>, /about, /kinda_roadmap, /cmdlist, /debug, /admin и пр.
        overlays.py      # /overlay/* (OBS browser sources)
        service_routes.py # OAuth callback, login
    api/                 # JSON-API
        user_api.py      # /api/user/* — основной API пользователя
        extension.py     # /api/extension/* — Twitch extension
        twitch_eventsub.py # EventSub webhook → публикует в MQTT
        slovotron_webhook.py # Словотрон webhook → публикует в MQTT
        user/            # подроутеры /api/user/*
            memealerts.py
            streamers.py # /api/user/streamers — список стримеров с фильтрами
            stats.py     # /api/user/stats — метрики мониторинга (графики)
services/                # бизнес-логика
    cache.py             # Cache (Redis)
    redis_state_manager.py # StateManager — стейт в Redis (cooldown, лурки, пирамидка)
    mqtt.py              # MQTTClient (aiomqtt) — pub-sub событий Twitch
    twitch.py            # Twitch-клиент
    eventsub_service.py  # обработка EventSub (подписка из MQTT)
    slovotron.py         # словотрон (обработка webhook'ов из MQTT)
    memes.py / memes_v2.py # Memealerts API (выдача мемкоинов, refresh токенов)
    statistics.py        # StatisticsService — сбор метрик мониторинга (Redis + дамп в БД)
    ...
twitch/                  # чат-бот
    chat/
        bot.py           # ChatBot — подписка на MQTT, диспетчер сообщений
        command_manager.py
        commands/        # реализации команд (!трусы, !кусь, !pat, !hug, !cmdlist, !тг, !дис, !tiktok, ...)
        base/            # базовые классы команд (Command, SimpleCDCommand, TargetCommand, SavingResultCommand)
        handlers/        # хендлеры сообщений (пирамидка, лурк, привет, "я бот?")
    state_manager.py     # SMParam, StateManager-обёртка
    client/twitch.py     # Twitch auth/HTTP-клиент
templates/               # Jinja2-шаблоны
static/                  # статика (CSS, JS, изображения)
    styles.css
    js/streamers.js      # /streamers page (фильтры + сортировка)
    js/stats.js          # /stats page (графики Chart.js, синхронизация фильтров с URL)
utils/                   # хелперы
    streamers.py         # список стримеров с фильтрами/сортировкой
    streamers_sort.py    # compute_streamer_score (рекомендуемая сортировка)
    overlays.py          # touch_overlay_usage — обновление overlays_last_usage
schemas/                 # Pydantic-схемы API
    api.py              # StatsType, StatsPeriod, StatsPoint/ResponseSchema и пр.
templates/
    stats.html          # /stats — страница графиков мониторинга
```

## Миграции БД

**Важно:** миграции создаются ТОЛЬКО через `alembic revision --autogenerate -m "title"`.

После генерации файла миграции — отредактируй его: alembic может «замечать»
несинхронизированный дрейф схемы (например, удалённые колонки, которые в реальности
нужно сохранить). Оставь в `upgrade()`/`downgrade()` только изменения, связанные с
твоей задачей.

## /streamers — фильтры и сортировка

- HTML-роут `/streamers` отдаёт заглушку без данных (для скорости); список
  загружается через `fetch('/api/user/streamers?...')` на клиенте (`static/js/streamers.js`).
- JSON-API: `/api/user/streamers` — query-параметры `sort` (recommended|followers|created|name),
  `order` (asc|desc), `f_bot`/`f_meme`/`f_ai`/`f_overlay`/`f_online`/`f_pants` (true|false|пусто).
- Общая логика фильтрации и сортировки — в `utils/streamers.py:get_streamers_list`.
- Скоринг «рекомендуемого» порядка — `utils/streamers_sort.py:compute_streamer_score`.
  Коэффициенты: chat_bot=3, memealerts=2, ai_stickers=4, is_live=10 и т.д.
- Поле `User.overlays_last_usage` обновляется при обращении к любому оверлею
  (`utils/overlays.py:touch_overlay_usage`), не чаще 1 раза в час на пользователя.
  Считается «использует оверлеи», если последнее обращение было не позднее 14 дней.

## Профиль стримера `/profile/<username>`

В разделе «Интеграции» отображаются статусы: Чат-бот, Memealerts, ИИ-стикеры, Оверлеи.
Статус «Оверлеи» вычисляется через `overlays_used_recently` (те же 14 дней).

## Обновление followers_count (TODO на будущее)

Сейчас `User.followers_count` обновляется только при логине пользователя
(`routers/web/service_routes.py:login_callback_task`). Для свежей сортировки по
фолловерам предлагается:

1. Раскомментировать запись в БД в `routers/web/pages.py` в роуте `/profile/<username>`
   (сейчас там `# TODO` и закомментированный `profile_user_dict["followers_count"] = ...`),
   добавив cache-key `followers:last_update:<user_id>` с TTL 24h — это обновит данные
   для часто просматриваемых профилей.
2. Добавить фоновую задачу в `dependencies.py:lifespan` через `scheduler.add_job`,
   которая раз в 12 часов берёт пачку пользователей (например, с самым старым
   `followers_count` или `interacted_at < now - 30 days`) и обновляет их через
   `twitch.get_followers`. Обязательно соблюдать rate-limit Twitch (на 429 — возвращать
   в очередь).

## Таблица активности `activity_streamer` / `activity_user` (план, не реализовано)

Сейчас учёт активности стримеров и юзеров отсутствует. Цель: выводить «самые активные
каналы» и «самые активные зрители» на `/streamers` и в профиле, а также использовать
для рекомендуемой сортировки.

План:
1. Модели в `database/models.py`:
   - `ActivityStreamer(user_id FK, total_count int, commands_count int, rewards_count int,`
     ` memealerts_count int, updated_at)`.
   - `ActivityUser(twitch_user_id int, channel_id FK, messages_count int,`
     ` commands_count int, rewards_count int, updated_at)`.
   - PK `(user_id)` / `(twitch_user_id, channel_id)`.
2. Миграция через `alembic revision --autogenerate -m "add activity tables"`.
3. Точки инкремента (через кэш `services.cache.Cache`, не напрямую в БД):
   - `twitch/chat/command_manager.py:CommandsManager.handle` — после успешной команды
     инкремент `commands_count` для канала и для юзера (по `chatter_user_id`).
   - `services/eventsub_service.py` — в `handle_reward_redemption` инкремент
     `rewards_count`/`memealerts_count` для канала и supporter-а.
   - (опц.) `twitch/chat/bot.py:on_message` — счётчик входящих сообщений для юзера
     (без блокировки хендлеров и команд).
4. Сервис батч-заливки (`services/activity.py`): копит инкременты в Redis-хэшах
   `activity:streamer:<id>` / `activity:user:<id>:<channel_id>`, а раз в N минут
   (APScheduler-джоб в `dependencies.py:lifespan`) заливает через `pg_insert(...).on_conflict_do_update()`
   батчем в одну транзакцию. Метод `inc(channel_id, user_id, *, commands=0, rewards=0, ...)`.
5. API: `/api/user/streamers` отдаёт `total_count`/`commands_count` для сортировки,
   `/api/user/profile/<name>` — топ-N активных юзеров канала.

## Подсистема статистики мониторинга (реализовано)

Полноценная подсистема метрик для графиков активности на странице `/stats`.
Модель `Statistics` (`database/models.py`), сервис `StatisticsService`
(`services/statistics.py`), API `/api/user/stats`, фронт `/stats`.

### Модель `Statistics`

Таблица `statistics`:
- `id` (serial PK, суррогатный — нужен только чтобы SQLAlchemy-маппер собрался).
- `bucket_ts` (timestamptz, NOT NULL) — начало 10-минутного бакета (UTC, округлено вниз).
- `type` (varchar(64), NOT NULL) — `message_incoming` | `message_outgoing` | `reward_memecoins` |
  `reward_ai_stickers` | `command_handled`.
- `subtype` (varchar(64), NOT NULL, default `""`) — для `reward_*`: `received`/`succeed`/`failed`/
  `success`/`failed_on_moderation`; для `command_handled` — имя команды; для `message_*` — `""`.
- `channel_id` (bigint, NULL) — twitch_id канала (на будущее для разбивки по каналам; в MVP всегда NULL).
- `count` (int, NOT NULL, default 0).

Уникальный индекс `statistics_pk` по `(bucket_ts, type, subtype, channel_id)` с
`NULLS NOT DISTINCT` (Postgres ≥ 15) — чтобы `ON CONFLICT DO UPDATE` корректно
схлопывал строки с `channel_id=NULL`. Доп. индексы: `ix_statistics_type_bucket`, `ix_statistics_bucket_ts`.

### `StatisticsService` (`services/statistics.py`)

- Счётчики накапливаются в Redis-хэшах `statistics:bucket:<ISO-UTC>` (один хэш на
  10-минутный бакет), поле `f"{type}:{subtype}:{channel_id or ''}"`, TTL 2ч.
  Инкремент — атомарный `hincrby` через `asyncio.create_task` (fire-and-forget, не
  блокирует обработку сообщений).
- `inc(type, subtype="", channel_id=None)` — точка инкремента. Метод **синхронный**
  (возвращает `None`), сам создаёт таску — вызывать без `await`.
- `flush_to_db()` — APScheduler-джоб раз в 10 мин (`dependencies.py`): SCAN'ит все
  `statistics:bucket:*` старше текущего бакета, для каждого `HGETALL` + батч
  `pg_insert(...).on_conflict_do_update(... count = old + EXCLUDED.count)`. После
  успешного INSERT ключ в Redis удаляется; при ошибке остаётся для переигрывания.
- `cleanup_old_data()` — APScheduler-джоб раз в сутки (04:00 UTC), удаляет строки
  старше 90 дней (`RETENTION_DAYS`).
- `get_chart(type, subtype, channel_id, period, dt_from, dt_to)` — возвращает ряд
  точек `(bucket_ts, count)` с **zero-fill пустых бакетов** внутри диапазона
  (чтобы график был непрерывным). Агрегация по периоду через Postgres `date_bin`.
  Диапазон жёстко ограничен по периоду (10m→1д, 1h→5д, 3h→14д, 6h→30д, 1d→90д).

### Точки инкремента

- `twitch/chat/bot.py:on_message` (после валидации схемы) → `inc("message_incoming")`.
- `twitch/chat/bot.py:send_message` (после успешной отправки) → `inc("message_outgoing")`.
- `twitch/chat/command_manager.py:handle` (после `cmd.handle`) → `inc("command_handled", subtype=cmd.command_name)`.
- `services/eventsub_service.py:reward_buy_memealerts` → `inc("reward_memecoins", subtype="received"|"succeed"|"failed")`.
- `services/eventsub_service.py:reward_ai_sticker` → `inc("reward_ai_stickers", subtype="received"|"success"|"failed_on_moderation")`.

Инъекция `StatisticsService` — через DI в `ChatBot` и `TwitchEventSubService`
(см. `container.py`); вызывается как `self._statistics.inc(...)` (без `await`).

### API `/api/user/stats`

`routers/api/user/stats.py` — `GET /api/user/stats` с `Security(user_auth)`.
Query: `type` (StatsType), `subtype`, `channel`, `period` (10m|1h|3h|6h|1d),
`from`/`to` (UTC ISO). Возвращает `StatsResponseSchema` (`{type, subtype, period, points}`).

Схемы в `schemas/api.py`: `StatsType` (StrEnum), `StatsPeriod` (StrEnum),
`StatsPointSchema` (`datetime` UTC, `value` int), `StatsResponseSchema`.

### Фронт `/stats`

`templates/stats.html` + `static/js/stats.js`: Chart.js (CDN v4), селекторы
type/subtype/period/dates. Фильтры синхронизируются с URL (shareable links).
Tooltip показывает значение + `Average RPS = value / bucket_seconds`.

### Метрика «среднее время обработки сообщения» (план, не реализовано)

Цель: среднее время (мс) от получения сообщения (`on_message`) до окончания
обработки, **не учитывая** намеренные `asyncio.sleep` (драматические паузы в
`IAmBotHandler`, розыгрыш `!трусы`) и fire-and-forget фоновые задачи
(`!трусы`'s `finish_raffle` через `asyncio.create_task` — уже не блокирует
`on_message`, поэтому автоматически исключается).

План:
1. Модель `Statistics(bucket_ts timestamptz, type varchar, subtype varchar | null,`
   ` count int)`. PK `(bucket_ts, type, subtype)`. Index по `type`, по `bucket_ts`.
2. Сервис `services/metrics.py`:
   - Держит в оперативке (или в Redis-хэше) счётчики `dict[(bucket_ts, type, subtype), int]`.
   - Метод `inc(type, subtype=None)` — вычисляет `bucket_ts = floor(now, 10min)`,
     инкрементит.
   - APScheduler-джоб раз в 10 минут (в `dependencies.py:lifespan`): сбрасывает кэш
     (`dict.copy()` + очистка), батчем INSERT в `Statistics`.
3. Точки вызова `metrics.inc(...)`:
   - входящее сообщение: `twitch/chat/bot.py:on_message` → `inc("message_incoming", null)`.
   - исходящее: `ChatBot.send_message` → `inc("message_outgoing", null)`.
   - награда мемкоинов: `services/eventsub_service.py` → `inc("reward_memealerts", null)`.
   - команда: `command_manager.handle` → `inc("command_usage", cmd.command_name)`.
   - и т.п.
4. API `/api/admin/stats?resolution=10m|1h|1d&type=...&subtype=...&from=...&to=...`:
   - SQL: `GROUP BY bucket_trunc(bucket_ts, resolution)` через `date_bin` (Postgres).
   - Возвращает `[{datetime, value}, ...]`.
5. UI: `templates/monitoring.html` + `static/js/monitoring_stats.js` (Chart.js из CDN).

## Обновление токенов Twitch пользователей (план, не реализовано)

Сейчас в `twitch/client/twitch.py` для каждого запроса от имени пользователя создаётся
новый `TwitchClient` и вызывается `set_user_authentication(access, scope, refresh)`.
`twitchAPI` v4.5 автоматически обновляет access-токен (`auto_refresh_auth=True` по
умолчанию), НО:
- новый токен после refresh **никуда не сохраняется** — при следующем запросе снова
  берётся устаревший `access_token` из БД и снова рефрешится через `validate=True`;
- если refresh-токен истёк или инвалидирован (смена пароля, дисконнект приложения) —
  `set_user_authentication` кидает `InvalidRefreshTokenException` (refresh истёк) или
  `UnauthorizedException` (оба токена невалидны). Сейчас исключение всплывает наружу и
  не обрабатывается централизованно.

Аналогия с `services/memes_v2.py:MemealertsOAuthService`:
- там токены хранятся в `MemealertsSettings` с `access_token`, `refresh_token`,
  `token_expires_at`, `token_refresh_expires_at`;
- `run_periodic_update()` по APScheduler'у раз в N дней выбирает строки, у которых
  refresh-токен скоро истечёт, и фоном делает `_request_tokens(refresh_token=...)`,
  сохраняя новые пары `access+refresh` в БД.

Реализация для Twitch (по образцу):
1. Миграция: в `twitch_bot_users` уже есть `access_token`, `refresh_token` (зашифрован
   через `EncryptedString`). Нужно добавить колонки `token_expires_at` (если её нет —
   проверь текущую схему) и `token_refresh_expires_at` для планирования обновлений.
   Access-токен у Twitch живёт ~4 часа, refresh — ~пользователь может отзывать сам.
2. Создать сервис `services/twitch_oauth.py` по образцу `MemealertsOAuthService`:
   - `_request_tokens(refresh_token)` — обёртка над `twitchAPI.oauth.refresh_access_token`,
     возвращает `(access_token, refresh_token)` плюс `expires_in` (из тела ответа нет
     `expires_in` для refresh? Twitch возвращает `expires_in` — нужно проверить).
   - `run_periodic_update()` — APScheduler-джоб в `dependencies.py:lifespan`, выбирает
     пользователей с `token_refresh_expires_at < now + 1 day` и обновляет их токены
     батчем через `asyncio.gather` с `Semaphore(N)` (Twitch rate-limited, ~30 req/min
     для `client_credentials` flow, для refresh-токенов лимиты лояльнее, но осторожно).
     На `InvalidRefreshTokenException` — помечаем пользователя как нуждающегося в
     повторном логине (флаг `User.needs_relogin = True` + уведомление при входе в чат).
     На `UnauthorizedException` — то же, но не пытаемся больше обновлять до повторного
     OAuth.
   - `_save_token(user_id, access, refresh, expires_at, refresh_expires_at)` — апдейт БД.
3. Использовать `user_auth_refresh_callback` в `twitch/client/twitch.py`:
   - `TwitchClient` принимает `user_auth_refresh_callback: Callable[[str, str], Awaitable[None]]`,
     который вызывается автоматически при каждом успешном refresh-е прямо в рантайме.
   - Передать туда асинхронную функцию, которая обновляет БД (через `pg_insert.on_conflict_do_update`).
   - Включить `auto_refresh_auth=True` (по умолчанию) и убрать ручной `validate=True`,
     если уверен в сохранённых `scope`.
4. Централизованная обработка `InvalidRefreshTokenException`/`UnauthorizedException`:
   - Обернуть все вызовы `set_user_authentication` в `twitch/client/twitch.py` в
     `try/except` и при инвалидном refresh — пометить `user.needs_relogin = True`,
     сбросить `access_token`/`refresh_token` в NULL, отписаться от EventSub
     (`twitch.delete_eventsub_subscription`) и при возможности написать в чат канала
     уведомление «награды и рейд отключены, перелогиньтесь в панели управления»
     (используя bot-токен, не юзерский).
   - Аналогично в `services/eventsub_service.py` при обработке награды: если упало с
     `InvalidRefreshTokenException` — не пытаться дальше, вернуть баллы и пометить
     награду отменённой.
5. Уведомление при смене пароля (`TODO.md` L11): Twitch не присылает событие напрямую,
   но `UnauthorizedException` кидается именно при инвалидации обоих токенов (что
   происходит при смене пароля/дисконнекте приложения). Ловим это исключение в
   централизованном обработчике (см. п.4) и при первом срабатывании шлём в чат
   сообщение через bot-токен (`ChatBot.send_message`).

Замечания по `twitchAPI`:
- `TwitchClient(...).set_user_authentication(token, scope, refresh_token, validate=True)`
  сам валидирует токен через `oauth.validate_token` и при 401 один раз делает
  `refresh_access_token`. Это означает что на каждый запрос к API Twitch сейчас
  выполняется лишний HTTP-запрос к `id.twitch.tv/oauth2/validate` — для оптимизации
  стоит передавать `validate=False` и доверять нашему фоновому обновлению, либо
  закэшировать результат валидации на TTL.
- `refresh_access_token` возвращает `(access_token, refresh_token)`, без `expires_in`.
  Twitch всё-таки возвращает `expires_in` в теле — нужно расширить обёртку в
  `services/twitch_oauth.py` и дёрнуть JSON напрямую через `httpx` (как
  `MemealertsOAuthService._request_tokens`), либо парсить из ответа twitchAPI.
- `InvalidRefreshTokenException` определён в `twitchAPI.type` — импортировать оттуда.

## Полезные файлы

- `container.py` — DI-контейнер, список wired-модулей.
- `dependencies.py` — lifespan приложения (запуск редиса, mqtt, шедулера).
- `routers/routers.py` — сборка всех роутеров.
- `routers/web/pages.py` — HTML-роуты, в т.ч. `/kinda_roadmap` (roadmap + текущие TODO) и `/cmdlist`.
- `utils/streamers_sort.py` — формула «рекомендуемой» сортировки.
- `twitch/chat/handlers/handlers.py` — не-командные хендлеры сообщений (привет, "я бот?", unlurk, пирамидка).
