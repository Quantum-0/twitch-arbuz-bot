# Баланс и история операций

> Справочник по фиче. При изменении поведения — обнови этот файл в том же коммите.

Внутренняя валюта стримера для ИИ-фич (сейчас — генерация ИИ-стикеров; списание
за TTS — в планах, `TODO.md`). Пополнение — только администратором.

## Модель

- `User.total_deposited` / `total_spent` (Numeric 12,2); `balance` — вычисляемая
  колонка `deposited − spent` (SQLAlchemy `Computed`).
- Готча: `user.balance` в шаблонах лениво загружается — после flush с UPDATE
  по users нужен прогрев (`routers/web/pages.py:_panel_context`); подробности и
  поведение в тестах — готчи в `AGENTS.md`.

## Операции

- **Пополнение**: `POST /api/admin/balance/deposit` (админ) — запись
  `BalanceOperation` делается ДО `db.execute(update)` (после него instance
  экспайрится, синхронный `user.*` даёт MissingGreenlet).
- **Списание**: генерация ИИ-стикера — сумма с наценкой
  `cost * ai.cost_multiplier + ai.cost_single_call`; при нехватке —
  `NegativeBalanceError` и возврат награды. Списание и запись операции — в одной
  транзакции с `total_spent` (`services/stickers.py:_save_to_db`).

## История (`balance_operations`)

Колонки: `twitch_id` (FK → `twitch_bot_users.twitch_id`), `type` (`deposit`/`spend`),
`action` (`admin_deposit`/`ai_sticker`/`initial_balance`), `amount` (при списании —
с наценкой), `balance_after` (снимок), `details` JSONB, `created_at` (naive-UTC).
Енумы — `schemas/enums.py:BalanceOperation*`. При внедрении истории каждому юзеру
с ненулевым балансом создана одна запись `initial_balance` («Инициализация истории
операций с текущим балансом»): deposit при положительном балансе, spend на
`|balance|` при отрицательном (овердрафт — иначе CHECK `amount > 0` ронял миграцию).

## API и UI

- `GET /api/user/balance/operations?before_id&limit` — курсорная пагинация по `id`
  (`routers/api/user/balance.py`).
- Страница `/balance-history` (`templates/balance_history.html` +
  `static/js/balance_history.js`): плашка текущего баланса, таблица
  Дата | Тип | Сумма | Действие | Баланс после, кнопка «Показать ещё».
- Входы: ссылка «История →» с панелек «Баланс» на `/panel/ai-stickers` и
  `/panel/tts`; пункт меню пользователя «История баланса» (виден только при
  ненулевом балансе, `templates/base.html`).
