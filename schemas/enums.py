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
    QUALITY = "quality"


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
