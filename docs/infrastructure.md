# Инфраструктура (прод-сервер vds-msk)

> Операционная справка по проду. При изменениях инфраструктуры — обнови этот файл.

Подключение к серверу — только по явному разрешению пользователя; редактировать
что-либо на сервере запрещено (правила — AGENTS.md «Запреты»). Сервер — прод.

## Приложение

Docker-контейнер приложения на vds-msk; деплой выполняет пользователь.
Перед любой миграцией БД на сервере — бэкап (см. ниже).

## Jaeger (трейсы)

- Compose в `~/jaeger/` (config `jaeger-config.yml`); приложение шлёт трейсы на
  `jaeger:4317` (`config.otel_endpoint`), семплирование 100%.
- Хранилище in-memory с кольцевым буфером `max_traces: 40000` — лимит подобран под
  memory-лимит; глубокой истории трейсов нет.
- Healthcheck: `wget http://127.0.0.1:13133/status` (важно: путь `/status`, не `/`).
- Шум OTel в GlitchTip: логгер `opentelemetry.exporter.otlp.proto.grpc.exporter`
  заглушен уровнем CRITICAL (`utils/logging_conf.py`) — `propagate=False` не
  спасает, т.к. sentry-sdk ≥2.x перехватывает логи monkeypatch'ем
  `logging.Logger.callHandlers`.

## Бэкапы

- Все бэкапы на сервере в `~/backup` (структура, команды, восстановление —
  `~/backup/README.md`).
- Ежедневный `pg_dump` БД `twitch_bot` — systemd-таймер `pg-backup.timer`
  (04:30 МСК + jitter, ротация 14 дней, логи `journalctl -u pg-backup.service`).
- **Перед любой миграцией на сервере** — из корня репо на сервере:
  `./backup-before-migrate.sh <лейбл>` (дамп `twitch_bot_pre-mig_*` не ротируется;
  откат — `pg_restore --clean --if-exists`).
- Офсайт-копии нет: бэкапы защищают от кривых миграций, но не от потери диска.

## Разовые CLI-скрипты

### Бэкфилл клипов в Telegram (`scripts/backfill_clips.py`)

Повторная отправка клипов/ярких моментов за период (например, после простоя
TG-сервиса): перезапрашивает клипы за окно у Twitch Helix и шлёт их в clips-чат
теми же payload'ами, что и поллер. Курсоры `last_clip_date`/`last_highlight_date`
не трогает. Дубликаты возможны — истории доставок нет.

Запуск на VDS внутри работающего контейнера (имя контейнера уточни через
`docker ps`; docker cp кладёт скрипт в контейнер без пересборки образа,
при пересоздании контейнера копия пропадёт):

```bash
cd ~/twitch-bot  # папка проекта на сервере
git pull

CONTAINER=$(docker ps -qf "name=app")  # контейнер приложения twitch-bot
docker cp scripts "$CONTAINER":/app/

# 1. Посмотреть, что уйдёт (без отправки, MQTT не подключается):
docker exec -it "$CONTAINER" python -m scripts.backfill_clips \
    --all --from 2026-11-09T00:00:00Z --include-highlights --dry-run

# 2. Боевой прогон (--to = момент восстановления TG-сервиса, чтобы не дублировать клипы после него):
docker exec -it "$CONTAINER" python -m scripts.backfill_clips \
    --all --from 2026-11-09T00:00:00Z --to 2026-11-10T00:00:00Z --include-highlights
```

Вместо `--all` можно перечислить логины: `--login quantum075 d_e_l_y`.
Ключи: `--from`/`--to` — окно по дате создания клипа (ISO, UTC), по умолчанию
`--to` = сейчас; `--include-highlights` — также яркие моменты.
