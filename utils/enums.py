from enum import StrEnum


class UserRole(StrEnum):
    DEFAULT = "default"
    BETA_TESTER = "beta-tester"
    OWNER = "owner"


class SSEChannel(StrEnum):
    AI_STICKER = "ai-sticker"
    MESSAGE = "msg"
    HEAT = "heat"