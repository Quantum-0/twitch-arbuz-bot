# Twitch-расширение (визуальная новелла)

> Справочник. При изменении поведения — обнови этот файл в том же коммите.

Расширение Twitch (panel-слот): интерактивный диалог с ботом — «визуальная
новелла» о сервисе (персонажи Quantum075Bot и Quantum075). Цель — рассказать
зрителю о боте и привести на сайт.

## Страницы (`routers/web/extension.py`)

- `/extension/panel.html` — зрительская панель (`static/ext_panel/panel.html`,
  работает в iframe расширения).
- `/extension/control.html` — координаторская (тот же файл, Cache-Control 7 дней).

## API (`routers/api/extension.py`, префикс `/api/extension`)

- `GET /scene/{channel_id}/{scene_key}` — сцена из графа `STORY_DATA` (текст,
  спикер, аватар, фон, `choices` → `next_step` | внешняя `link`). Динамика:
  - `bot_users` — подставляет число пользователей + top-5 стримеров (скоринг
    `utils/streamers_sort.py:compute_streamer_score`, онлайн-статус из кэша
    Redis/Twitch, лимит 500);
  - `try_command` — кнопки только включённых у стримера команд (кусь/лизь/банан);
    если все выключены — сцена-заглушка `try_command_unavailable`.
- `POST /chat/{channel_id}` — отправка сообщения в чат от имени расширения:
  whitelist-регулярка (только `!кусь|!лизь|!банан` + `кого-нибудь|стримера|
  @Quantum075Bot`), rate-limit 12 сообщений/мин на канал (Redis), Helix
  `send_extension_chat_message`.

Аутентификация запросов — EBS-токен Twitch: Bearer JWT, подпись HS256
(`settings.twitch.extension_secret`), проверка совпадения `channel_id`
(`verify_extension_token`).
