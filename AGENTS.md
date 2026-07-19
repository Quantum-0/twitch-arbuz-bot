# AGENTS.md

Краткая шпаргалка для агентов, работающих с этим репозиторием.

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
services/                # бизнес-логика
    cache.py             # Cache (Redis)
    redis_state_manager.py # StateManager — стейт в Redis (cooldown, лурки, пирамидка)
    mqtt.py              # MQTTClient (aiomqtt) — pub-sub событий Twitch
    twitch.py            # Twitch-клиент
    eventsub_service.py  # обработка EventSub (подписка из MQTT)
    slovotron.py         # словотрон (обработка webhook'ов из MQTT)
    memes.py / memes_v2.py # Memealerts API (выдача мемкоинов, refresh токенов)
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
utils/                   # хелперы
    streamers.py         # список стримеров с фильтрами/сортировкой
    streamers_sort.py    # compute_streamer_score (рекомендуемая сортировка)
    overlays.py          # touch_overlay_usage — обновление overlays_last_usage
schemas/                 # Pydantic-схемы API
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

## Полезные файлы

- `container.py` — DI-контейнер, список wired-модулей.
- `dependencies.py` — lifespan приложения (запуск редиса, mqtt, шедулера).
- `routers/routers.py` — сборка всех роутеров.
- `routers/web/pages.py` — HTML-роуты, в т.ч. `/kinda_roadmap` (roadmap + текущие TODO) и `/cmdlist`.
- `utils/streamers_sort.py` — формула «рекомендуемой» сортировки.
- `twitch/chat/handlers/handlers.py` — не-командные хендлеры сообщений (привет, "я бот?", unlurk, пирамидка).
