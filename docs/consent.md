# Юридические документы и согласия (ФЗ-152)

> Справочник. При изменении поведения — обнови этот файл в том же коммите.
> Саммари закона 152-ФЗ — `docs/FZ-152.md`.

- **Документы:** `templates/legal/privacy.html` (Политика конфиденциальности) и
  `templates/legal/agreement.html` (Пользовательское соглашение). Роуты `/privacy`,
  `/agreement` в `routers/web/pages.py` (`user_auth_optional`, передают `user`).
- **Согласие перед авторизацией:** на `main.html` отдельно подтверждаются
  Пользовательское соглашение (`#agreement`) и согласие на обработку ПД
  (`#personalDataConsent`). Кнопка `#twitchLoginBtn` заблокирована (`disabled`),
  пока не установлены обе отметки. После подтверждения — переход на `/login`.
- **Cookie-баннер:** в `templates/base.html` (флаг залогиненности —
  `<meta name="logged-in">`). Согласие хранится в `localStorage`
  (`cookie_consent` = `accepted`/`rejected`) для всех; для залогиненных
  дополнительно фиксируется в БД (`User.cookie_consent_at`). Яндекс.Метрика
  загружается только при значении `accepted`; выбор «Только необходимые»
  не завершает сессию.
- **Boosty-предложение:** модал в `base.html`, только для залогиненных.
  Не показывается минимум 35 дней после закрытия/перехода
  (`User.boosty_dismissed_at`). Переходы на Boosty считаются в `User.boosty_clicks`
  (грубая оценка факта подписки — API Boosty нет). Данные отдаются в шаблон через
  `window.__userBoostyDismissed`.
- **API consent/boosty:** `routers/api/user/consent.py`, подключён в
  `routers/api/user_api.py`, префикс `/api/user/consent` (`/cookie-consent`,
  `/boosty/dismiss`, `/boosty/click`), `user_auth`.
- **Выход:** `GET /logout` в `routers/web/service_routes.py` —
  `request.session.clear()` + редирект на `/`.
- **Миграция:** `7c2a1f4e9d30_user_cookie_consent_and_boosty_tracking.py`
  (колонки `twitch_bot_users`: `cookie_consent_at`, `boosty_dismissed_at`,
  `boosty_clicks`).
