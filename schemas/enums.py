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
