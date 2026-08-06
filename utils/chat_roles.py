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
    """Определить роль чаттера по старшинству: Streamer > Mod > VIP > Sub > Bot > Chatter.

    Боты не имеют badge у Twitch — классифицируются по списку KNOWN_BOTS (только если
    нет role-badge выше: например, если бот является модератором канала, он классифицируется
    как модератор).
    """
    set_ids = {b.set_id for b in badges}
    if "broadcaster" in set_ids:
        return ChatterRole.STREAMER
    if "moderator" in set_ids:
        return ChatterRole.MODERATOR
    if "vip" in set_ids:
        return ChatterRole.VIP
    if "subscriber" in set_ids:
        return ChatterRole.SUBSCRIBER
    if login.lower() in KNOWN_BOTS:
        return ChatterRole.BOT
    return ChatterRole.CHATTER
