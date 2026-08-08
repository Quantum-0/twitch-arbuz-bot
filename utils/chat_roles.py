"""Классификация чаттера по роли (для TTS-матрицы разрешений) и список известных ботов."""

from __future__ import annotations

from schemas.enums import ChatterRole
from schemas.twitch import ChatMessageBadge

# Известные боты Twitch (по @login в нижнем регистре, без «@»).
# Источник: twitch/chat/base/target_command.py + собственный бот.
# TODO: расширять список при обнаружении новых ботов.
KNOWN_BOTS: frozenset[str] = frozenset(
    {
        "streamelements",
        "wisebot",
        "wizebot",
        "wsbot",
        "alurarin",
        "nightbot",
        "botrixoficial",
        "dustyfox_bot",
        "moobot",
        "jeetbot",
        "fossabot",
        "lavrikbot",
        "quantum075bot",
    }
)


def classify_chatter(badges: list[ChatMessageBadge], login: str) -> ChatterRole:
    """Определить роль чаттера по старшинству: Streamer > Bot > Mod > VIP > Sub > Chatter.

    Боты классифицируются по списку KNOWN_BOTS или по значку бота (set_id == "bot")
    ДО проверки модератора — бот с badge модератора всё равно классифицируется как бот.
    Только broadcaster (стример) имеет приоритет над ботом.
    """
    set_ids = {b.set_id for b in badges}
    if "broadcaster" in set_ids:
        return ChatterRole.STREAMER
    if login.lower() in KNOWN_BOTS or "bot" in set_ids:
        return ChatterRole.BOT
    if "moderator" in set_ids:
        return ChatterRole.MODERATOR
    if "vip" in set_ids:
        return ChatterRole.VIP
    if "subscriber" in set_ids or "founder" in set_ids:
        return ChatterRole.SUBSCRIBER
    return ChatterRole.CHATTER
