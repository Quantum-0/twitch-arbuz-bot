# Инструкция по полному удалению Memealerts v1

Использовать этот документ после того, как все пользователи перейдут на нативную интеграцию Memealerts v2. До этого момента v1 необходимо сохранять только как fallback для уже подключённых пользователей.

## Цель

Полностью удалить возможность и runtime-поддержку ручной интеграции Memealerts v1, сохранив нативную интеграцию v2 и начисление мемкоинов через неё.

## Условия начала работ

- Убедиться по данным production, что у пользователей не осталось активных v1-подключений без v2 OAuth-токенов.
- Сделать резервную копию базы данных.
- Проверить, что все активные `memealerts_settings.access_token` и `refresh_token` относятся к v2.
- Согласовать удаление legacy-данных и выполнить миграцию в окно обслуживания.

## План изменений

1. В `services/eventsub_service.py` удалить ветку, которая вызывает `MemealertsService` при отсутствии v2 `access_token`.
2. Удалить fallback с v2 на `MemealertsService.give_bonus()`.
3. Оставить только получение токена через `MemealertsOAuthService` и вызов `MemealertsV2Service.give_bonus()`.
4. Удалить из `TwitchEventSubService` зависимость `MemealertsService`, поле `_memealerts` и соответствующий аргумент в `container.py`.
5. Удалить provider `memealerts` и импорт `MemealertsService` из `container.py`.
6. Удалить deprecated route `POST /api/user/memealerts` из `routers/api/user_api.py` вместе с legacy-импортами, которые больше нигде не используются.
7. Удалить deprecated web route `/memealerts-tutorial` из `routers/web/pages.py` и файл `templates/memealerts-tutorial.html`.
8. Удалить упоминание `/memealerts-tutorial` из `routers/robots/for_robots.py` и связанные тесты или фикстуры.
9. Удалить `services/memes.py` после проверки, что на него больше нет импортов.
10. Удалить `utils/memes.py` после проверки, что на него больше нет импортов.
11. Удалить из `database/models.py` колонку `_memealerts_token`, свойство `memealerts_token` и импорты `encrypt_value`/`decrypt_value`, если они больше не используются в этом файле.
12. Создать Alembic-миграцию командой `poetry run alembic revision --autogenerate -m "remove legacy memealerts token"`.
13. Проверить миграцию вручную: в `upgrade()` должно быть только удаление `memealerts_settings.memealerts_token`, а в `downgrade()` — восстановление этой nullable-колонки.
14. Не удалять `memealerts_reward`, `coins_for_reward`, склонения названия валюты и v2 OAuth-колонки.
15. Удалить старые v1-аналитику, JavaScript-код ручного токена и неиспользуемые стили только после поиска оставшихся ссылок.
16. Обновить или удалить тесты, проверяющие v1 route, tutorial и fallback.

## Проверки после изменений

- Выполнить поиск по репозиторию по строкам `memealerts_token`, `MemealertsService`, `token_expires_in_days`, `/api/user/memealerts`, `/memealerts-tutorial`.
- Убедиться, что v2 routes `/memealerts/auth`, `/memealerts/callback`, `/api/user/memealerts`, `/api/user/memealerts/reward` работают.
- Запустить `poetry run alembic upgrade head` на тестовой базе.
- Запустить `poetry run ruff check .`.
- Запустить `poetry run mypy` для затронутых Python-модулей.
- Проверить подключение v2, создание/исправление/отключение Twitch-награды и успешное начисление мемкоинов.
- Отдельно проверить, что при ошибке v2 больше не выполняется вызов v1 и награда корректно отменяется.

## Важные ограничения

- Не удалять колонку базы данных до удаления всех runtime-обращений к ней.
- Не удалять общий endpoint настройки количества мемкоинов: он используется v2.
- Не менять исторические Alembic-миграции; добавлять отдельную новую миграцию.
- Не выполнять удаление на production без резервной копии и подтверждения, что пользователи мигрировали.
