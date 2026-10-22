from enum import StrEnum


class ChatbotDefaultTargetBehaviour(StrEnum):
    TIP = "tip"
    RANDOM = "random"
    STREAMER = "streamer"


class FileStorageDir(StrEnum):
    AI_GENERATED_STICKER = "ai-gen-stickers"
    REFS = "refs"


class AIStickerModel(StrEnum):
    MINI = "mini"
    FLARE = "flare"
    SUNBURST = "sunburst"


class AIReferenceUsagePolicy(StrEnum):
    DENY = "deny"
    WITH_MY_CHARACTER = "with_my_character"
    ALLOW = "allow"


class ChatterRole(StrEnum):
    """Роль чаттера по старшинству (используется для матрицы TTS-разрешений)."""

    STREAMER = "streamer"
    MODERATOR = "moderator"
    VIP = "vip"
    SUBSCRIBER = "subscriber"
    BOT = "bot"
    CHATTER = "chatter"


class TTSTrigger(StrEnum):
    """Способ триггера TTS (колонки матрицы разрешений). Награда — отдельно."""

    ALL = "all"
    ALL_NO_REPLIES = "all_no_replies"
    STREAMER_TAG = "streamer_tag"
    COMMAND = "command"


class BanFeature(StrEnum):
    """Функция, ограничиваемая баном (список `bans.features`).

    Зрительские фичи точечно блокируются в точках enforcement:
    - ``chat_bot`` — команды и хендлеры чат-бота (`ChatBot.on_message`);
    - ``memecoins`` — награда начисления мемкоинов;
    - ``ai_stickers`` — награда генерации ИИ-стикера;
    - ``tts`` — награда TTS;
    - ``site`` — доступ к сервису как таковой: редирект на `/banned`
      (используется при бане стримера).
    """

    CHAT_BOT = "chat_bot"
    MEMECOINS = "memecoins"
    AI_STICKERS = "ai_stickers"
    TTS = "tts"
    SITE = "site"


# Все зрительские фичи — дефолт для канального бана через !бан.
VIEWER_BAN_FEATURES: frozenset[BanFeature] = frozenset(
    {
        BanFeature.CHAT_BOT,
        BanFeature.MEMECOINS,
        BanFeature.AI_STICKERS,
        BanFeature.TTS,
    }
)

# Полный бан (все зрительские фичи + доступ к сайту) — выдаётся автоматически
# при глобальном бане зарегистрированного пользователя (стримера).
FULL_BAN_FEATURES: frozenset[BanFeature] = VIEWER_BAN_FEATURES | {BanFeature.SITE}


class BanScope(StrEnum):
    """Радиус действия бана: весь сервис или конкретный канал."""

    GLOBAL = "global"
    CHANNEL = "channel"


class BanIssuerRole(StrEnum):
    """Кто выдал бан (аудит; влияет на права управления баном)."""

    ADMIN = "admin"
    MODERATOR = "moderator"
    STREAMER = "streamer"
    CHAT = "chat"
