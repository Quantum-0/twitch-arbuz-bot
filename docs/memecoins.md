# Мемкоины Memealerts (награда за баллы канала)

> Справочник по интеграции Memealerts. При изменении поведения — обнови этот
> файл в том же коммите. Описание внешнего API Memealerts — `docs/memealerts-api.md`.

Зритель выкупает награду канала «Получить мемкоины», указав в вводе награды свой
ник/ID на memealerts.com → бот начисляет `coins_for_reward` мемкоинов саппортеру
стримера через API Memealerts от имени стримера.

## Подключение (панель /panel/memealerts)

- **OAuth v2**: per-user токены Memealerts, шифрованы в `memealerts_settings`;
  авто-refresh (`MemealertsOAuthService`, `services/memes_v2.py`: Redis-лок на
  юзера, ретраи, cron */3ч).
- **Награда** создаётся/подписывается из панели: `memealerts_reward` (reward_id),
  стоимость награды, `coins_for_reward` — сколько мемкоинов начислять.
- **Кастомные склонения** названия валюты канала: поля `ma_*` (авто-склонения
  Memealerts) vs `*_override` (ручные) — для сообщений в чат.
- **История пополнений**: таблица `memealerts_deposits` + таблица в панели
  (`GET /api/user/memealerts/deposits`).
- Команда `!мемалёртс` — ссылка на страницу Memealerts стримера (только при
  подключённой интеграции).

## Обработка выкупа награды (`services/eventsub_service.py:reward_buy_memealerts`)

1. Бан-гейт по фиче `memecoins` (см. `docs/bans.md`) → отмена redemption
   (возврат баллов) + сообщение зрителю.
2. Дедуп: insert-on-conflict в `memealerts_deposits` по UNIQUE `redemption_id`
   (статус `pending`) — повторные доставки вебхука не начисляют дважды.
3. Поиск саппортера стримера: по 24-hex ID из ввода → в кэше БД
   (`memealerts_supporters`) → перебор саппортеров стримера (API).
4. Начисление `give_bonus` от имени стримера → `fulfill` redemption; при ошибке →
   `cancel` redemption (возврат баллов зрителю).
5. Финальный статус в `memealerts_deposits`: `succeeded` / `failed` + код ошибки
   (не саппортер, не найден, токен истёк…). Сообщение в чат с правильным
   склонением валюты.

## v1 → v2

v1 (общий токен, `services/memes.py`) — deprecated, работает как fallback для
старых юзеров. Идёт принудительная миграция на v2: уведомление показывается
первых N пользователям (порог в коде, повышается по мере миграции). План полного
удаления v1 — `REMOVE_MEMEALERTS_V1.md` в корне репо.

Ключевые файлы: `services/memes_v2.py`, `services/eventsub_service.py`,
`routers/api/user/memealerts.py`, `routers/web/memealerts_routes.py`,
`database/models.py` (MemealertsSettings, MemealertsSupporter, MemealertsDeposit).
