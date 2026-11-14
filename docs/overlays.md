# Оверлеи OBS и SSE

> Справочник по фиче. При изменении поведения — обнови этот файл в том же коммите.

Оверлеи — браузерные страницы для OBS (Browser Source); события приходят по SSE,
часть оверлеев интерактивна (Heat — клики зрителей по экрану). Конструктор ссылок —
панель `/panel/overlays`.

## Каталог оверлеев

| Оверлей | Путь | Что делает |
|---|---|---|
| Звёздочка | `/overlay/star` | интерактивная звезда на ниточке: Heat-клики, шанс оторвать; позиция/размер/цвет; управление из чата |
| Салют | `/overlay/fireworks` | фейерверк, параметры частиц/гравитации, управление из чата |
| XP Error | `/overlay/xp-error` | шуточные BSOD-окна (кулдаун/масштаб/звук) |
| Найди пару | `/overlay/pair` | игра: карточки с лицами стримеров сервиса/Twitch-эмодзи/стикеров; сетки 3×3–5×5, 7 тем + кастомные цвета (`GET /overlay/pair/deck`), рестарт без перезагрузки |
| Jumping Chibi | `/overlay/jumping-chibi` | прыгающий чиби-персонаж (таймер) |
| Start Wait | `/overlay/start-wait` | заглушка «стрим скоро начнётся» |
| AI-стикер | `/overlay/ai-sticker` | показ сгенерированного стикера (см. `docs/ai-stickers.md`) |
| TTS | `/overlay/tts` | озвучка чата (см. `docs/tts.md`) |
| Словотрон | `/overlay/slovotron` | игра «угадай слово» (см. ниже) |
| Яндекс.Музыка | `/overlay/ya-music-widget` | текущий трек (типы pulsma / battlebeats; работает с приложением Pulse Sync) |
| Halloween | `/overlay/halloween` | сезонный: листья/тыквы/паук, реакции на чат/фоллоу/сабы/рейды, команды `!шуш`/`!фуф`/`!дуть`; виден только двум логинам (quantum075, denfalcone) |

## SSE-инфраструктура

- Роут `GET /sse/{user_id}/{channel}` (`routers/sse.py`); `user_id` — Twitch channel
  ID стримера. Аутентификации нет (кроме `slovotron` — требует `?secret=<UUIDv3>`).
- Каналы (`utils/enums.py:SSEChannel`): `ai-sticker`, `heat`, `slovotron`, `msg`
  (сообщения чата для chat-control оверлеев), `tts`, `twitch-events`
  (follow/sub/raid — алерты).
- `SSEManager` (`services/sse_manager.py`) хранит активные подключения в памяти,
  `broadcast()` → `put_nowait` во все очереди канала (неблокирующе). Grace-период
  15с после дисконнекта (микро-разрывы EventSource не отменяют награды).
  `snapshot()` — мгновенный срез для метрики `sse_connections`.
- Оверлеи/внешние утилиты авторизуются `overlay_secret` стримера; сброс —
  `/panel/other` (`POST /api/user/overlay/reset-secret`).

## Heat (интерактивные клики)

Twitch-расширение Heat (id `cr20njfkgll4okyrhag7xxph270sqk`) — клики зрителей по
оверлею. `services/heat_upstream.py`: один websocket-коннект к Heat-серверу на
user_id, лениво создаётся при подключении SSE-клиента на канал `HEAT`, глушится
при отключении; события кликов проксируются в SSE с reconnect-backoff. Установка
расширения из панели (`GET /api/user/install-heat`) + проверка активности
(`GET /api/user/check/heat-installed`).

## EventSub-алерты (канал twitch-events)

Подписки follow/subscribe/raid «overlay-managed»: `POST/DELETE /api/user/eventsub`
по заголовку X-Overlay-Secret; события → SSE `twitch-events`. Джоба
`cleanup_overlay_eventsub` (раз в 3 мин, `services/overlay_eventsub_cleanup.py`)
отписывает подписки с истёкшим heartbeat при отсутствии SSE-клиентов.

## Временные команды оверлеев

`register_custom_command` (`routers/web/overlays.py:get_temp_commands`) — оверлей
при открытии сам регистрирует временные команды (TTL 15 мин в Redis), чтобы они
появлялись в `/cmdlist` (например, команды управления звёздочкой).

## Словотрон

Внешняя игра «угадай слово» (slovotron.fra3a.ru, автор fra3a). Роут `/overlay/slovotron`
редиректит на внешний сайт, всегда добавляя `obs-overlay=1&transparent_background=1`
(прозрачный фон для OBS; в ссылке оверлея в панели параметры не видны). Вебхуки
`POST /api/webhook/slovotron` (auth по overlay_secret канала): `game-new` (новое
слово) и `game-win` (победитель) → SSE-канал `slovotron`; при победе бот пишет в
чат «@победитель угадывает слово «X»! Поздравляем!». Dock-панель для OBS:
`/dock/{channel}/slovotron`. API-кнопки `/api/user/slovotron/tip|restart` — бот
пишет `!подсказка` / `!словотрон-рестарт` в чат. Файлы: `services/slovotron.py`,
`routers/api/slovotron_webhook.py`, `schemas/slovotron.py`.
