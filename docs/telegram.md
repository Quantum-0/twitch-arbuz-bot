# Telegram-интеграция: уведомления о начале/окончании/перезапуске стрима

> **Дата фиксации поведения:** 2026-10-04.
>
> **Правило:** при любом изменении поведения Telegram-интеграции (настройки, кейсы
> падения, топики MQTT, ключи Redis, колонки БД) — **обязательно обнови этот файл**
> в том же коммите/PR. Устаревшая документация хуже отсутствующей.

## Статус реализации (на дату фиксации)

Спецификация ниже — **целевая модель**, согласованная с владельцем. Реализация:

- ✅ 2026-10-04, в проде: доставка результатов `telegram/result/{request_id}`
  (раньше результаты не доходили из-за несовпадения топиков).
- ✅ 2026-10-04, имплементировано в коде (деплой ожидает): целевая модель целиком —
  дебаунс `stream.offline` (отложенная джоба `stream_offline_deferred:*` +
  проверка Get Streams), notify-CD (`telegram:restart_notify_cd:*`),
  упразднение Redis-маркера `telegram:stream_restart:*` и edit-ветки,
  настройка `Settings.stream_restart_window_minutes`.
- После деплоя на прод — актуализировать этот список.

## Архитектура и смежный сервис

```
twitch-bot (vds-msk)  ⇄  MQTT/EMQX (префикс топиков: twibot/)  ⇄  twibot-tg (vds-ams)  →  Telegram Bot API
```

- **Основной сервис** (этот репозиторий): EventSub Twitch, настройки, логика
  уведомлений, планировщик (APScheduler, SQL jobstore).
- **TG-микросервис** — соседний репозиторий: **`../twibot-tg`**
  (локально: `/Users/notamedia/PycharmProjects/twibot-tg`, прод: ssh `vds-ams`,
  `~/twibot-tg`, docker compose, контейнер `twibot-tg`). aiogram 3: long polling,
  выполняет MQTT-команды → Bot API, отдаёт результаты и события о подключении
  чатов. HTTP API: `/api/healthcheck`, `/api/connect` (генерация deep-link),
  `/api/chat_status` (проверка, что бот не кикнут), debug-ручки под API-ключом.

Всё, что касается отправки/удаления/редактирования сообщений и подключения
чатов, нужно искать в **обоих** репозиториях.

## Общая константа W

`W` — окно перезапуска, задержка подтверждения окончания и CD сообщений о
рестарте. Одна величина на всё:

- Конфиг: `Settings.stream_restart_window_minutes` (`config.py`),
  env `STREAM_RESTART_WINDOW_MINUTES`, **дефолт 15**, в `.env` не задаём.

## Настройки (таблица `telegram_settings`)

Колонки стрим-уведомлений:

| Колонка | Значения | Смысл |
|---|---|---|
| `stream_notification_enabled` | bool | Мастер-выключатель уведомлений о стриме |
| `stream_chat_id` | str | Привязанный чат (канал/группа/приват) |
| `stream_message_template` | text | Шаблон «начало стрима»: `{streamer}` `{title}` `{category}` `{link}` |
| `stream_offline_behavior` | `delete` \| `message` \| `keep` | Действие при **подтверждённом** окончании: удалить пост о начале / отправить «Завершён» / ничего |
| `stream_offline_message_template` | text | Шаблон «окончание»: `{streamer}` `{link}` |
| `stream_restart_behavior` | `notify` \| `silent` \| `edit` | Поведение при перезапуске < W (см. ниже; `edit` ≡ `silent`, сохранён для совместимости; дефолт новых записей — `silent`) |
| `stream_restart_message_template` | text | Шаблон «перезапуск»: `{streamer}` `{link}` |
| `stream_link_preview_enabled` | bool | Превью ссылок в сообщениях |
| `last_stream_message_id` | str | TG message_id текущего поста «начало стрима» (для `delete`) |
| `last_stream_offline_message_id` | str | TG message_id последнего «Завершён» (в целевой модели записывается, но не читается) |

Дефолтные тексты: `🔴 {streamer} начинает стрим!` + title/category/link,
`⚪️ Стрим завершён.`, `🟠 Стрим упал, но был перезапущен.` При ошибке
форматирования шаблона — fallback на дефолт (с warning в лог).

Прочие группы той же таблицы (вне рамок этого документа): чаты и настройки
клипов (`clips_*`, `last_clip_date`), AI-стикеров (`stickers_*`), заготовки
моста чатов (`twitch_to_tg_enabled`, `tg_to_twitch_enabled`, `telegram_user_id`).

## Целевая модель поведения

Термин «**подтверждённое окончание**» — стрим офлайн по EventSub И проверка
`GET /helix/streams` через W минут это подтвердила.

### Событие `stream.offline` (любые настройки)

Ничего не отправляет и не удаляет. Только ставит отложенную джобу:

- APScheduler date-джоба `stream_offline_deferred:{user_id}`, SQL jobstore
  (переживает рестарт приложения), `run_date = now + W`,
  `replace_existing=True` (серия падений перезапускает таймер),
  `misfire_grace_time=None` (исполнить даже с опозданием).
- `last_stream_message_id` **не** чистится — он нужен джобе.

### Событие `stream.online`

1. Пытаемся отменить джобу `stream_offline_deferred:{user_id}`
   (try/except JobLookupError):
   - **Отменили** → это перезапуск < W, офлайн не «настоящий»:
     - `silent` / `edit` → **тишина**. Пост «начало стрима» остаётся
       единственным сообщением, ничего не удаляем и не редактируем, пока не
       будет реального окончания.
     - `notify` → **сразу** отправить сообщение о перезапуске (restart-шаблон)
       и записать Redis-ключ CD с TTL = W. Если ключ ещё активен — молчим
       (анти-спам: не чаще одного сообщения о рестарте за W).
   - **Не отменили** (джобы не было) → новый стрим (> W или первый):
     обычный пост «начало стрима», `message_id` сохраняется в
     `last_stream_message_id` через `telegram/result`.

### Отложенная джоба (через W после offline)

1. Перечитать настройки; выключены/чат отвязан → выйти.
2. `GET /helix/streams`: стрим **онлайн** → тихо выйти (гонка «offline пришёл
   позже online» / потерянное событие online). Если API недоступен (статус
   неизвестен) → перезапланироваться на 2 мин, всего до 3 попыток.
3. Стрим **офлайн** → подтверждённое окончание, выполнить
   `stream_offline_behavior`:
   - `delete` → удалить пост «начало стрима» (`last_stream_message_id`);
   - `message` → отправить «Завершён» (`request_id = stream_offline:{user_id}`);
   - `keep` → ничего.
4. Обнулить только `last_stream_message_id` (offline-id заполняет колбэк
   доставки «Завершён»).

### Пример таймлайна (notify, offline=message)

| Время | Событие | Что в TG |
|---|---|---|
| 15:00 | стрим начался | пост «🔴 начинает стрим» |
| 16:00 | упал | — (джоба на 16:15) |
| 16:01 | поднялся | «🟠 упал, но был перезапущен» + CD до 16:16 |
| 16:02 | упал | — (джоба перезаписана) |
| 16:03 | поднялся | — (CD активен) |
| 16:30 | упал | — (джоба на 16:45) |
| 16:35 | поднялся | «🟠 упал, но был перезапущен» (CD истёк) |
| 17:00 | завершился | — (джоба на 17:15) |
| 17:15 | джоба: офлайн подтверждён | «⚪️ Стрим завершён» |

Следствие дебаунса: «Завершён»/удаление поста приходят с задержкой W после
фактического конца — осознанно принятое решение.

## Кейсы падения и пограничные ситуации

| Кейс | Поведение |
|---|---|
| Обычный стрим: начало → конец | Пост «начало» → через W подтверждение → offline-действие |
| Рестарт < W, режим silent/edit | Тишина; в чате остаётся только пост «начало стрима» |
| Рестарт < W, режим notify | Сообщение о рестарте, не чаще 1 за W (Redis-CD) |
| Серия падений/рестартов | Каждое новое падение перезаписывает джобу (+W); notify-CD глушит повторы |
| Рестарт > W | Джоба уже исполнена → это **новый стрим**: обычный пост «начало» (+ «Завершён» остаётся, если был) |
| Гонка: Twitch прислал offline **позже** нового online | Джоба через W проверит Get Streams → стрим онлайн → тихо выйти, пост не трогаем |
| Событие online потерялось при возврате | Джоба увидит «онлайн» в Get Streams → тихо выйти; сообщение не дублируется |
| Рестарт приложения бота | Джобы в SQL jobstore сохраняются; Redis-CD и TTL переживают рестарт |
| Redis недоступен | CD не проверится — возможен редкий дубль notify-сообщения; джобы не зависят от Redis |
| Get Streams недоступен в момент срабатывания джобы | До 3 проверок с интервалом 2 мин; после — окончание не применяется (error в лог), деструктивных действий нет |
| Настройки выключили / чат отвязали во время pending | Джоба при исполнении видит выключенные настройки и выходит |
| Twitch не прислал offline вовсе | Окончание не детектится (как раньше); пост «начало» остаётся висеть |

## Redis-ключи

| Ключ | TTL | Назначение |
|---|---|---|
| `telegram:restart_notify_cd:{user_id}` | W | CD сообщений о рестарте (режим notify): наличие = недавно писали, молчим |
| `telegram:stream_restart:{user_id}` | W | Рестарт-маркер **старой модели** (до имплементации дебаунса). В целевой модели упраздняется — удалить при реализации |

Другие части интеграции (pending deep-link подключения чатов и т.п.) живут в
памяти процесса twibot-tg, не в Redis.

## Таблицы БД

| Таблица | Использование |
|---|---|
| `telegram_settings` | Все настройки и привязки чатов, `last_stream_message_id`, `last_stream_offline_message_id` (см. выше) |
| `twitch_bot_users` | Связка user_id ↔ twitch_id (для EventSub-условий и Get Streams) |
| `apscheduler_jobs` | SQL jobstore APScheduler, в т.ч. джобы `stream_offline_deferred:{user_id}` |

## MQTT-топики

Публикация обоими сервисами идёт с префиксом `twibot/`. twitch-bot подписан на
`telegram/result/+`, `telegram/chat_connected`, `telegram/chat_disconnected`;
twibot-tg — на `twibot/telegram/+`.

### twitch-bot → twibot-tg (команды, JSON по схемам `schemas/telegram.py`)

| Топик | Payload | Для чего |
|---|---|---|
| `telegram/send_message` | `SendMessageRequest` | Посты «начало», «Завершён», «перезапущен» |
| `telegram/send_photo` | `SendPhotoRequest` | AI-стикеры (TG-сервис сам скачивает файл по URL) |
| `telegram/send_video` | `SendVideoRequest` | Клипы (режим video) |
| `telegram/send_document` | `SendDocumentRequest` | Стикеры/клипы документом |
| `telegram/delete_message` | `DeleteMessageRequest` | offline-`delete` |
| `telegram/edit_message` | `EditMessageRequest` (+ `fallback_message_text`) | Редактирование сообщений; при неудаче TG-сервис шлёт fallback как новое |
| `telegram/leave_chat` | `LeaveChatRequest` | Отвязка чата (бот покидает чат) |

### twibot-tg → twitch-bot (события и результаты)

| Топик | Payload | Для чего |
|---|---|---|
| `telegram/result/{request_id}` | `SendResult` (`request_id`, `success`, `message_id`, `error`) | Результат любой отправки; `handle_telegram_result` сохраняет message_id в БД |
| `telegram/chat_connected` | `{user_id, scope, chat_id, chat_type, chat_title}` | Привязка чата (scope: stream/clips/stickers); создаёт EventSub-подписки |
| `telegram/chat_disconnected` | `{user_id, scope, chat_id}` | Бота кикнули/он ушёл: очистка привязки, снятие EventSub |

### Конвенция request_id

- `stream_online:{user_id}` — пост «начало стрима» → `last_stream_message_id`;
- `stream_offline:{user_id}` — «Завершён» → `last_stream_offline_message_id`;
- `sticker:{user_id}:{sticker_id}` — AI-стикеры;
- `uuid4` — прочие одиночные команды (delete/edit без сохранения id).

Значения не содержат `/` — request_id безопасен как один уровень топика
`telegram/result/{request_id}`.

## Подключение EventSub и сверка

`stream.online` / `stream.offline` создаются при включении тогла (есть чат) и
при `chat_connected` (тогл уже включён). Раз в час
`reconcile_stream_subscriptions` пересоздаёт слетевшие подписки; при неудаче
снимает `stream_notification_enabled`, чтобы юзер видел неработающий тогл.
При отвязке stream-чата — подписки снимаются, тоглы и message_id сбрасываются.
