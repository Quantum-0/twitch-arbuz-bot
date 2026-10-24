from enum import StrEnum


class UserRole(StrEnum):
    DEFAULT = "default"
    BETA_TESTER = "beta-tester"
    OWNER = "owner"


class SSEChannel(StrEnum):
    AI_STICKER = "ai-sticker"
    MESSAGE = "msg"
    HEAT = "heat"
    SLOVOTRON = "slovotron"
    TTS = "tts"
    TWITCH_EVENTS = "twitch-events"


class MQTTTopic(StrEnum):
    """Топики MQTT для подписок (wildcard-паттерны как в EMQX)."""

    CHAT_MESSAGES = "twitch/+/message"
    REWARD_REDEMPTIONS = "twitch/+/reward-redemption"
    SLOVOTRON = "slovotron/+/+"
    TELEGRAM_CHAT_CONNECTED = "telegram/chat_connected"
    TELEGRAM_CHAT_DISCONNECTED = "telegram/chat_disconnected"
    TELEGRAM_RESULT = "telegram/result/+"
