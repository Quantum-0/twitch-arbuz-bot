"""Фейковые ответы /api/* для фронтенда без бэка.

Используются дев-сервером (server.py отвечает ими на /api/*) и браузерными
тестами (helpers.mock_api перехватывает запросы ещё в браузере). Реальные
внешние сервисы (twitch/memealerts/ai-tunnel) не вызываются никогда.
"""

from typing import Any

OK_MESSAGE = {"title": "Мок", "message": "Сохранено (мок-режим без бэка)"}

# Ответы GET /api/user/check/<name>: формат CheckStatusResponseSchema.
CHECK_RESPONSES: dict[str, dict[str, Any]] = {
    "/api/user/check/sse": {"result": True, "problems": [], "warnings": []},
    "/api/user/check/heat-installed": {"result": True, "problems": [], "warnings": []},
    "/api/user/check/memealerts-token": {
        "result": True,
        "problems": [],
        # warning — чтобы в дев-режиме было видно жёлтое состояние карточки
        "warnings": ["welcome-bonus выключен на Memealerts (мок)"],
    },
    "/api/user/check/memealerts-reward": {
        "result": False,
        "problems": [],
        "warnings": [],
        "state": "missing",
    },
    "/api/user/check/ai-stickers-reward": {"result": True, "problems": [], "warnings": []},
    "/api/user/check/telegram": {
        "result": True,
        "problems": [],
        "warnings": ["какой-то нюанс (мок)"],
    },
    # Специально для харнесса компонентов (tests/front/static/components.html):
    # карточка с проблемой токена — рендер allowlist-ссылки «Переавторизуйтесь».
    "/api/user/check/reauth": {
        "result": False,
        "problems": ["twitch_token_invalid"],
        "warnings": [],
        "state": "missing",
    },
    "/api/user/check/tts-overlay": {"result": True, "problems": [], "warnings": []},
    "/api/user/check/tts-reward": {"result": True, "problems": [], "warnings": []},
    "/api/user/check/tts-server": {"result": True, "problems": [], "warnings": []},
}

TELEGRAM_SETTINGS: dict[str, Any] = {
    "stream_chat_id": 100,
    "stream_chat_title": "Мок-канал",
    "stream_notification_enabled": True,
    "stream_link_preview_enabled": True,
    "stream_offline_behavior": "keep",
    "stream_restart_behavior": "silent",
    "stream_message_template": "",
    "stream_offline_message_template": "",
    "stream_restart_message_template": "",
    "clips_chat_id": None,
    "clips_chat_title": None,
    "clips_enabled": False,
    "clips_mode": "all",
    "clips_delivery": "link",
    "stickers_chat_id": 200,
    "stickers_chat_title": "Мок-личка",
    "stickers_enabled": True,
    "stickers_mode": "photo",
}


# GET /api/admin/flowviz: снимок live-скоростей карты потоков (FlowVizResponseSchema).
# Числа правдоподобные, чтобы в дев-режиме схема «жила».
FLOWVIZ_RESPONSE: dict[str, Any] = {
    "ts": 0,
    "rates": {
        "message_incoming": {"": 12.4},
        "message_outgoing": {"": 3.1},
        # чат идёт через EventSub channel.chat.message — число совпадает
        # с message_incoming (иначе карта врёт на ребре twitch→бот)
        "eventsub_events": {"channel.chat.message": 12.4, "stream.online": 0.01},
        "heat_proxy_messages": {"": 6.8},
        "http_requests": {"api": 1.7, "panel": 0.4},
        "db_queries": {"SELECT": 4.2, "INSERT": 0.3, "UPDATE": 0.2},
        "redis_ops": {"get": 22.5, "set": 8.1, "hincrby": 5.0},
        "mqtt_publish": {"telegram": 0.02, "system": 0.0},
        "mqtt_messages": {"telegram": 0.02},
        "sse_events": {"heat": 6.8, "twitch-events": 0.1},
        "twitch_api_calls": {"helix": 0.8, "oauth": 0.01},
    },
    "sse": {"total": 14, "unique_users": 9, "unique_pairs": 11, "heat": 8},
    "active_channels": 37,
}


def default_for(method: str, path: str) -> tuple[int, dict[str, Any] | list[Any]]:
    """Дефолтный мок-ответ для запроса. Неизвестные ручки — тоже мок, чтобы
    дев-сервер не падал и фронт не ходил дальше нас."""
    if method == "GET":
        if path in CHECK_RESPONSES:
            return 200, CHECK_RESPONSES[path]
        if path == "/api/user/telegram/settings":
            return 200, TELEGRAM_SETTINGS
        if path == "/api/user/memealerts/reward":
            return 200, {"cost": 5000}
        if path == "/api/user/staff-badges":
            return 200, {"server_alerts": 0, "pending_references": 0}
        if path == "/api/user/streamers":
            return 200, []
        if path == "/api/admin/flowviz":
            return 200, FLOWVIZ_RESPONSE
        return 200, {"result": True, "problems": [], "warnings": []}
    return 200, OK_MESSAGE
