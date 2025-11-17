from enum import StrEnum


class UserRole(StrEnum):
    DEFAULT = "default"
    BETA_TESTER = "beta-tester"
    OWNER = "owner"
