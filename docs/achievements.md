# Ачивки (достижения стримеров)

> Геймификация сервиса. При изменении поведения — обнови этот файл в том же коммите.

Стримеры получают ачивки за действия на сайте и в Twitch. Условия получения
**не показываются** — на публичном профиле (`/profile/{login}`) отображается
только панель «Достижения» с уже полученными ачивками (иконка + название,
уровневые — с римской цифрой в углу). Нет ачивок — панели нет. Уведомлений
никаких: всё тихо, пользователь замечает сам.

## Механика

Два способа начисления (реестр — `utils/achievements.py:ACHIEVEMENTS`):

- **Событийные** (`AchievementsService.unlock`) — запись в момент события:
  пасхалка «сборщик точек», обрыв звёздочки зрителем, выигрыш собственных трусов.
- **Ленивые** (`AchievementsService.evaluate_and_get`) — вычисляются из БД при
  просмотре любого профиля — для просматриваемого пользователя: лайки,
  покупки мемкоинов, ай-стикеры, state-флаги. Вызов идёт из
  `routers/web/pages.py:profile_page`.

**Правило заморозки**: источник ленивой ачивки дёргается только пока ачивка не
достигла максимального уровня. Одноуровневая ачивка после записи не
перепроверяется никогда; уровневая — до последнего уровня. Уровень только
растёт (upsert c `GREATEST`) — ачивка не может пропасть или понизиться, даже
если значение источника упало (например, зритель снял лайк). У пользователя
с полным набором просмотр профиля не дергает источники ачивок вообще
(только два коротких SELECT по `user_achievements`).

Ретроактивность: ленивые ачивки зачитываются по всей доступной истории в БД
(первый просмотр профиля после деплоя выдаёт всё накопленное). Событийные
начинают начисляться с момента деплоя; история покупок/стикеров ограничена
датами появления соответствующих таблиц.

Уровни: `thresholds = (5, 10, 25)` → I/II/III по значению-счётчику
(`level_for`), пустые → одноуровневая. Римская цифра — HTML-чип поверх
иконки (не в SVG), т.к. меняется с уровнем.

## Состав (14)

| Код | Название | Уровни | Механизм | Источник/хук |
|---|---|---|---|---|
| `easter_dots` | Ловец точек | — | событие | `POST /api/user/achievements/easter-dots` из `dots-easter-egg.js:trigger()` (только залогиненным) |
| `star_torn` | Звезда с неба | — | событие | `POST /api/user/achievements/star-break` из `star.js` (heat-обрыв зрителем; `X-Overlay-Secret`, rate-limit 1/10 мин) |
| `pants_kept` | Вернул своё | — | событие | `pants.py:finish_raffle` — победитель == цель == владелец канала, участников ≥ 5 |
| `all_rewards` | Полный комплект | — | лениво | все 3 награды созданы (`memealerts_reward` + `ai_sticker_reward_id` + `tts_reward_id`) |
| `memecoins_sold` | Мем-магнат | 10/25/100 | лениво | count `memealerts_deposits` (status=succeeded) |
| `likes_given` | Добрая душа | 5/10/25 | лениво | count `user_likes` (from; передаётся из profile_page) |
| `likes_received` | Всеобщая любовь | 5/10/25 | лениво | count `user_likes` (to) |
| `ai_stickers_used` | Стикер-мастер | 1/10/50 | лениво | count `balance_operations` (spend, ai_sticker) |
| `donated` | Поддержал проект | — | лениво | `User.donated > 0` |
| `beta_tester` | Бета-тестер | — | лениво | `User.in_beta_test` |
| `veteran` | С нами год | — | лениво | `created_at` ≥ 365 дней |
| `social_links` | Везде и всюду | — | лениво | `Links`: telegram+youtube+tiktok+discord (`SOCIAL_LINK_FIELDS`) |
| `telegram_linked` | На связи | — | лениво | `TelegramSettings.telegram_user_id` |
| `overlay_used` | Декоратор | — | лениво | `overlays_last_usage` не пуст |

## Таблица и отображение

- `user_achievements`: строка только для полученной ачивки — `twitch_id`
  (FK CASCADE), `code`, `level` (1..N), `unlocked_at` (получение), `updated_at`
  (последнее повышение). UNIQUE `(twitch_id, code)`. Миграция `6c48e386da09`
  (backfill не нужен — ленивое вычисление покрывает ретроактивность).
- Панель «Достижения» на профиле: `templates/profile.html` (под основным
  блоком), стили `.profile-achievements` в `static/styles.css`, стрелки
  прокрутки — `static/js/profile-achievements.js` (появляются при
  переполнении ленты; новые ачивки слева — сортировка `unlocked_at DESC`).
- Иконки: `static/images/achievements/<код>.svg` — единый стиль медальонов;
  заменить иконку = заменить файл.
- Ачивки участвуют в скоринге рекомендаций `/streamers`: +0.05 к скору за
  каждую полученную (`achievements_count` в `utils/streamers.py`,
  `compute_streamer_score` в `utils/streamers_sort.py`).

## Как добавить ачивку

1. Запись в `ACHIEVEMENTS` (`utils/achievements.py`) + SVG-иконка.
2. Точка начисления: событие → `unlock()` в месте события; состояние/счётчик
   из БД → строка в `AchievementsService._evaluate`/`_query_values`.
3. Для «не можем» из бэклога — сначала источник данных (см. ниже).

## Бэклог: идеи (партия C)

Добавляются записью в реестр + источником:

- `deposits_unique` «Народный любимец» (1/5/15 уникальных зрителей покупали
  мемкоины) — `count(distinct chatter_twitch_id)` по `memealerts_deposits`.
- `night_owl` «Ночной художник» — ай-стикер, сгенерированный в 00:00–05:00
  (hour из `balance_operations.created_at`).
- `sticker_marathon` «Марафон» — 10 стикеров за один день (group by date).
- `overlay_variety` «Коллекционер оверлеев» — требует инструментации: сейчас
  хранится один `overlays_last_usage` без типа; начать логировать тип в
  `touch_overlay_usage` (уже принимает `channel_name`) — считать только
  события после деплоя.

## Бэклог: не можем сейчас (партия D)

- **История покупок/стикеров/розыгрышей/звёздочки/пасхалки до появления
  таблиц и событий** — нигде не хранилась.
- **Ачивки по чат-активности зрителей** («зрители написали N сообщений на
  канале») — `Statistics.channel_id` всегда NULL в точках инкремента; нужно
  прокидывать `channel_id` в `StatisticsService.inc` по всему коду.
- **Twitch-события вне бота** (подписки, рейды как триггеры) — нет
  EventSub-подписок на эти темы.
- **«Стример посмотрел чужой стрим»** — данных нет.
- **Кто именно сорвал звёздочку** — Heat-ID анонимны (для текущей ачивки
  не важно).

Ключевые файлы: `utils/achievements.py`, `services/achievements.py`,
`routers/api/user/achievements.py`, `routers/web/pages.py` (профиль),
`twitch/chat/commands/pants.py`, `database/models.py` (UserAchievement),
`static/js/dots-easter-egg.js`, `static/js/star.js`,
`static/js/profile-achievements.js`.
