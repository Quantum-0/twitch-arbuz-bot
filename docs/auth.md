# Авторизация и токены

> Справочник. При изменении поведения — обнови этот файл в том же коммите.

## Twitch OAuth (основной вход)

- `GET /login` → редирект на Twitch authorize; `GET /login-callback`
  (`routers/web/service_routes.py`): обмен `code` → access/refresh (twitchAPI),
  `get_self`, upsert `User`, `session["user_id"]` → редирект в `/panel`.
  `GET /logout` — очистка сессии.
- Токены шифруются при записи в БД property-сеттерами `EncryptedString`
  (`database/models.py`); хранится и `twitch_token_expires_at`.
- Фоновая `login_callback_task` (после логина): подтягивание фолловеров,
  `set_bot_moder` (бот становится модератором канала — только на этом этапе),
  сверка EventSub-подписок (raid, награды мемкоинов/стикеров, stream.online/offline)
  и отписка лишних. Ранний выход при выключенных фичах запрещён — иначе старые
  подписки не отписываются. Дополнительно `follow_check.py` фоново перепроверяет
  `followed_to_admin` (троттл 30 мин).

## Refresh токенов

`services/twitch_token_service.py`: фоновый refresh (cron */6ч) с порогом 10 минут
до истечения, per-user Redis-лок, ретраи. `TwitchTokenExpiredError` → юзеру
показывается «переавторизуйтесь». Имя/аватар юзера обновляются при перелогине.

## Уровни доступа (`routers/security_helpers.py`)

- `user_auth` — залогиненный; `user_auth_optional` — аноним или юзер.
  Оба проверяют site-бан (см. `docs/bans.md`).
- `staff_auth` — модераторы/админ: `utils/staff.py:is_staff` (тот же список
  логинов, что у модерации референсов). Видны «Сервер», «Модерация», бейджи.
- `admin_auth` — HTTP Basic, только админ (`ADMIN_LOGIN = quantum075`).

## Другие провайдеры

- **Steam OpenID**: `/auth/steam` → `/auth/steam/callback` → привязка профиля
  (команда `!стим`, ссылка в `!ссылки`); отвязка `POST /api/user/steam/unlink`
  (`routers/web/steam_routes.py`).
- **Telegram Login OIDC**: `/auth/telegram/*` — привязка личного TG; обмен code
  и JWKS идёт через релей twibot-tg (`services/telegram_oidc.py`, `docs/telegram.md`).
- **Memealerts OAuth**: `/memealerts/auth` → `/memealerts/callback`
  (см. `docs/memecoins.md`).

Ключевые файлы: `routers/web/service_routes.py`, `routers/security_helpers.py`,
`services/twitch_token_service.py`, `database/models.py` (User, EncryptedString).
