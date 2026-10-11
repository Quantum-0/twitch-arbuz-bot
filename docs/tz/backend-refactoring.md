# ТЗ: Рефакторинг бэкенда — EventSub, TG-MQTT-хелперы, тесты, mypy

Статус: черновик, ожидает аппрува. Исполнитель: отдельный агент-контекст.
Обязателен к прочтению перед началом: `AGENTS.md`, `docs/telegram.md`.

## Контекст

2026-10-11 стрим-уведомления выделены из `TwitchEventSubService` в
`services/stream_notifications.py` (файл сократился 1228 → ~760 строк). Ниже —
найденные при ревью точки для продолжения, по убыванию ценности. Задачи
независимы, можно брать по одной.

## Задача 1. Декомпозиция `reward_buy_memealerts`

`services/eventsub_service.py` (`reward_buy_memealerts`, ruff C901 = 16 > 10,
~130 строк): лестница из 7 except-веток `MA*Error`, каждая повторяет
`_inc_reward("failed") → send_message → _cancel_redemption`.

Что сделать:
- таблицу «тип ошибки → текст юзеру» (dict), единый `except MAError`-хендлер
  с ней; специальную логику (`MADuplicateUserError`, v1/v2-fallback) оставить
  явными ветками;
- v1/v2-флоу начисления вынести в приватный метод (например `_give_ma_bonus`);
- не менять тексты сообщений и порядок cancel/fulfill — юзерский UX.

Почему: самое сложное место файла, любое изменение наград сейчас рискованно
без охватных тестов.

## Задача 2. Общий MQTT-хелпер отправки TG-сообщений

`SendMessageRequest`-payload собирается вручную в 3 местах:
`services/eventsub_service.py:_publish_send_message` (revocation/autofulfill),
`services/stream_notifications.py:_publish_send_message` (+edit/delete),
`services/clips_poller.py:_mqtt_publish_send_message`.

Что сделать: вынести общий модуль (например `services/telegram_mqtt.py`) с
функциями `send_message` / `edit_message` / `delete_message` (mqtt-клиент и
payload по `schemas/telegram.py`, валидация Pydantic-схемой перед publish);
перевести все 3 call-site на него.

Почему: дублирование контракта MQTT — риск рассинхрона полей при эволюции
payload (уже расходятся: edit поддерживает fallback, клипы — нет).

## Задача 3. Юнит-тесты `services/stream_notifications.py`

Новая логика без тестов: дебаунс offline, отмена джобы при рестарте, notify-CD,
Get Streams-проверка в джобе, ретраи. Conftest `tests/unit` уже работает
(2026-10 починен), образцы моков — `tests/unit/test_eventsub_revocation.py`,
`fixtures/twitch_mock.py`.

Что покрыть (mock scheduler/cache/mqtt/db):
- рестарт < W при silent/edit → тишина, джоба снята; при notify → сообщение,
  повторный рестарт в окно CD → молчание;
- джоба: Get Streams онлайн → выход без действий; офлайн → behavior
  delete/message/keep; live=None → ретрай-джоба с attempt+1, после 3-й — стоп;
- stream.online без джобы → обычный пост, request_id `stream_online:{user_id}`.

## Задача 4. Mypy-долги (поэтапно)

В проекте ~100 mypy-ошибок. Стартовая точка — `_get_user_by_id_or_login`
(`services/eventsub_service.py`, 4 ошибки var-annotated/selectinload): фикс по
образцу `services/stream_notifications.py:_get_user` (аннотация
`sa.Select[tuple[User]]`). Дальше — файлы, которых касается текущая задача;
отдельным коммитом — не массово.

## Задача 5 (опционально). Вынос revocation-уведомлений

`notify_revocation` + константы `_REVOCATION_*` из `TwitchEventSubService` →
отдельный модуль (тесты `test_eventsub_revocation.py` уже есть — переезд
безопасен). Рассмотреть, если eventsub_service снова станет тесно.

## Границы (НЕ делать)

- Не менять поведение стрим-уведомлений (спека — `docs/telegram.md`; при
  изменениях обновлять её в том же коммите).
- Не чинить 40 заведомо сломанных юнит-тестов (актуальный список — вывод
  `poetry run pytest tests/unit -q` на 2026-10-11) — только не ухудшать.
- Не трогать twibot-tg без отдельного разрешения.
- Без git commit/push и без деплоя.

## Критерии приёмки

- `poetry run pytest tests/unit -q` — не хуже текущего базлайна (119 passed).
- `poetry run ruff check` + `poetry run mypy` по изменённым файлам — без новых
  ошибок.
- Для задачи 2: grep по `"telegram/send_message"`-payload-дубликатам пуст.
