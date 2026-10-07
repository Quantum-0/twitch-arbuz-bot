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
- ✅ 2026-10-06, имплементировано в коде (деплой ожидает): привязка личного
  Telegram (`telegram_user_id`) — приватные чаты по deep-link + Telegram Login
  (OIDC, кнопка в «Других настройках»); TG-уведомления при отзыве авторизации
  Twitch (revocation) с дедупом на 24 ч.
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
моста чатов (`twitch_to_tg_enabled`, `tg_to_twitch_enabled`).

## Привязка личного Telegram-аккаунта (`telegram_user_id`)

`telegram_user_id` — Telegram user id юзера (он же chat_id его личного чата
с ботом). Используется для **персональных уведомлений о проблемах интеграций**
(сейчас — об отзыве авторизации Twitch, см. ниже). Заполняется двумя путями:

1. **Приватный чат по deep-link** (stream/clips/stickers): при
   `chat_connected` с `chat_type == "private"` chat_id приватного чата =
   Telegram user id → пишем в `telegram_user_id`
   (`services/telegram_integration.py:handle_chat_connected`). Группы/каналы
   не заполняют поле (там chat_id — id чата, а не юзера).
2. **Telegram Login (OpenID Connect)**, core.telegram.org/bots/telegram-login:
   кнопка «Привязать Telegram» в «Других настройках» панели →
   `GET /auth/telegram` (state + PKCE S256 в сессии) → oauth.telegram.org →
   `GET /auth/telegram/callback` → обмен code на `id_token` (Basic auth
   client_id:client_secret) → валидация JWT (RS256/ES256 по JWKS, `iss`,
   `aud`, `exp`) → claim `id` → `telegram_user_id`.
   Сервис: `services/telegram_oidc.py` (`TelegramLoginService`), роуты:
   `routers/web/telegram_routes.py`. Конфиг: `telegram_login_client_id`
   (числовой id бота, выдаёт @BotFather → Login Widget),
   `telegram_login_client_secret`, `telegram_login_redirect_url`
   (дефолт `https://bot.quantum0.ru/auth/telegram/callback`).
   Scope: `openid profile telegram:bot_access` (последний — разрешение боту
   писать юзеру в личку). Если client_id/secret не заданы — кнопка в панели
   скрыта, флоу отключён.
   Отвязка: `POST /api/user/telegram/unlink-account` (кнопка «Отвязать
   Telegram» в панели) — чистит только `telegram_user_id`, привязки чатов
   не трогает.

## Уведомления при отзыве авторизации Twitch (EventSub revocation)

`services/eventsub_service.py:handle_revocation` после основной обработки
(отключение фич) fire-and-forget вызывает `notify_revocation(broadcaster_id,
sub_type)` (`asyncio.create_task` — сбой TG не влияет на 204-ответ Twitch):

- Один реальный отзыв порождает **несколько** revocation-callback'ов
  (chat.message + redemption.add + ...) → дедуп: атомарный (SET NX EX) Redis-ключ
  `revocation_notified:{user_id}`, TTL 24 ч (через `Cache.set_str_nx`, фактический
  ключ в Redis — с префиксом `cache:`; при недоступном Redis дедуп пропускается).
- Сообщение уходит в личный чат: `telegram_user_id`; нет привязки — тихий выход.
- `request_id = revocation:{user_id}` (без `/`); результат доставки не
  обрабатывается (`handle_telegram_result` его игнорирует — message_id
  не сохраняется).
- Тексты по типу подписки: `channel.chat.message` → «Чат-бот отключён: …»,
  `channel.channel_points_custom_reward_redemption.add` → «Награды отключены: …»,
  прочие (stream.*, raid, follow, `user.authorization.revoke`) → «Twitch-интеграция
  отключена: …». Во всех — «Twitch отозвал авторизацию. Переавторизуйтесь на
  bot.quantum0.ru».
- Стрим-типы (`stream.online`/`stream.offline`): дополнительно снимается тогл
  `stream_notification_enabled` (переиспользуется
  `_disable_stream_notification`) — **до** guard'а привязки, чтобы сброс
  работал и без личной привязки TG. Привязка стрим-чата не очищается
  (TG-сторона жива): после переавторизации достаточно включить тогл обратно.
- Все ошибки внутри `notify_revocation` логируются и глушатся.

## Авто-фикс «автоматически выполнять» у наград (reward autofulfill)

`TwitchResourceNotFound` в `_cancel_redemption` / `_fulfill_redemption`
(`services/eventsub_service.py`) означает, что redemption уже закрыт на стороне
Twitch. Две причины: юзер включил у награды «автоматически выполнять»
(`should_redemptions_skip_request_queue` — бот тогда не может ни подтверждать,
ни возвращать баллы), либо гонка дублей вебхука (бот сам закрыл redemption по
первой доставке). Обработчики fire-and-forget вызывают
`notify_reward_autofulfill(user, reward_id)`:

1. `fix_reward_autofulfill` (`twitch/client/twitch.py`) читает фактический флаг
   награды и, если он включён, **выключает его сам** (`update_custom_reward`,
   только этот флаг). `False` (уже выключен / награда удалена) — тихий выход:
   это гонка дублей вебхука, уведомлять и чинить нечего. Фикс выполняется и без
   личной привязки TG.
2. Уведомление в личный чат (`telegram_user_id`): «…бот не мог подтверждать и
   возвращать баллы… Мы выключили эту галочку сами…». Дедуп — атомарный
   Redis-ключ `reward_autofulfill_notified:{twitch_id}:{reward_id}` (TTL 24 ч,
   `Cache.set_str_nx`); нет привязки — только фикс, без сообщения.
   `request_id = reward_autofulfill:{twitch_id}` — результат доставки
   не обрабатывается.

Ошибки логируются и глушатся (не ломают обработку redemption).

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
| `revocation_notified:{user_id}` | 24 ч | Дедуп TG-уведомлений об отзыве авторизации Twitch (один отзыв = несколько callback'ов); `user_id` — twitch_id |
| `reward_autofulfill_notified:{user_id}:{reward_id}` | 24 ч | Дедуп TG-уведомлений об авто-фиксе «автоматически выполнять» у награды; `user_id` — twitch_id |
| `telegram:stream_restart:{user_id}` | W | Рестарт-маркер **старой модели** (до имплементации дебаунса). В целевой модели упраздняется — удалить при реализации |

Ключи `revocation_notified:*` и `reward_autofulfill_notified:*` пишутся через
`Cache` (`services/cache.py`) — в Redis хранятся с префиксом `cache:`.

Другие части интеграции (pending deep-link подключения чатов и т.п.) живут в
памяти процесса twibot-tg, не в Redis.

## Таблицы БД

| Таблица | Использование |
|---|---|
| `telegram_settings` | Все настройки и привязки чатов, `last_stream_message_id`, `last_stream_offline_message_id`, `telegram_user_id` (личный TG-аккаунт, см. выше) |
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
- `revocation:{user_id}` — TG-уведомление об отзыве авторизации Twitch
  (`user_id` — twitch_id); результат доставки не обрабатывается;
- `reward_autofulfill:{user_id}` — TG-уведомление об авто-фиксе «автоматически
  выполнять» у награды (`user_id` — twitch_id); результат доставки
  не обрабатывается;
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
