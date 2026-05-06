from enum import StrEnum


class ChatbotDefaultTargetBehaviour(StrEnum):
    TIP = "tip"
    RANDOM = "random"
    STREAMER = "streamer"


class FileStorageDir(StrEnum):
    AI_GENERATED_STICKER = "ai-gen-stickers"
    REFS = "refs"
