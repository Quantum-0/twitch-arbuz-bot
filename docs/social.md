# Профили, лайки, каталог стримеров, поиск

> Справочник. При изменении поведения — обнови этот файл в том же коммите.

## Профиль (`/profile/{login}`)

Публичная страница стримера (`templates/profile.html`): аватар, лайк-кнопка,
фолловеры, онлайн-статус, ранг TwitchTracker, лайки полученные/отданные, блок
интеграций (Memealerts, TG и пр.), персонаж (референс) и ИИ-стикеры — если
разрешены настройками приватности юзера (см. `docs/ai-stickers.md`).

**Лайки:** `PUT/DELETE /api/user/likes/{target_user_id}` (нельзя лайкать себя),
таблица `user_likes`.

## Каталог стримеров (`/streamers`)

- HTML `/streamers` отдаёт заглушку; список грузится через
  `fetch('/api/user/streamers?...')` (`static/js/streamers.js`).
- API: `sort` (recommended|followers|created|interacted|likes|name), `order`
  (asc|desc), фильтры `f_bot`/`f_meme`/`f_ai`/`f_overlay`/`f_online`/`f_pants`.
- Логика: `utils/streamers.py:get_streamers_list`. Скоринг:
  `utils/streamers_sort.py:compute_streamer_score`. `User.overlays_last_usage`
  обновляется в `utils/overlays.py:touch_overlay_usage` (не чаще 1/час).
  «Использует оверлеи» = обращение не позднее 14 дней.
- **Поиск по имени** — на клиенте (`#streamersSearch`, debounce): подстрока ИЛИ
  Levenshtein (порог `max(1, len(q)/3)`). Запрос синхронизируется с URL (`?q=`).
  На бэк не ходит.
- **Имя под аватаркой** изгибается по дуге (SVG `textPath`, концентричной кругу
  аватара): разметка в `#streamerCardTemplate`, id пути уникализируется в JS
  (`setArcName`), обрезка 20 символов + «…», цвет `var(--streamer-name-color)`.

## Нечёткий поиск пользователей

- **Механизм:** `utils/user_search.py:search_users` — два этапа: SQL-пул
  кандидатов через pg_trgm (`login_name % q OR ILIKE`, GIN-индекс
  `ix_twitch_bot_users_login_trgm`, объявлен в `User.__table_args__`, чтобы
  autogenerate его не дропал) → переранжирование `rapidfuzz`.
- **API:** `GET /api/user/search?q&limit&context` (`routers/api/user/search.py`,
  `user_auth`). `context=bans` — исключает стафф (`utils/reference_moderation.py`)
  + `KNOWN_BOTS` + самого юзера и добавляет активных чаттеров его канала
  (`ChatBot.get_last_active_users`, группировка по `name.lower()`);
  `deposit`/`generic` — без исключений.
- **Виджет подсказок:** `static/js/user-suggest.js` (дропдаун под инпутом,
  стрелки/Esc, подсказки не строгие). Используется на `/bans` (логин таргета)
  и в `/admin` (пополнение).
