from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa

from config import settings
from database.models import GeneratedImage, User

StickersMode = Literal["mine", "with_me", "from_me"]


def build_stickers_query(
    mode: StickersMode,
    twitch_id: int,
    login: str,
    before: datetime | None = None,
    limit: int = 10,
) -> sa.Select:
    """Построить запрос последних ИИ-стикеров в зависимости от режима вкладки.

    Возвращает строки (GeneratedImage, channel_login) через outerjoin с User по on_channel.

    mine     — стикеры, сгенерированные на канале стримера (WHERE on_channel = twitch_id).
    with_me  — стикеры с других каналов, в промпте которых упоминается @<login>.
    from_me  — стикеры с других каналов, redeemed пользователем с логином <login>.
    """
    channel_id = int(twitch_id)
    q = (
        sa.select(GeneratedImage, User.login_name.label("channel_login"))
        .outerjoin(User, User.twitch_id == sa.cast(GeneratedImage.on_channel, sa.String))
        .where(GeneratedImage.file_id.is_not(None))
        .where(
            GeneratedImage.created_at > sa.func.now() - sa.text(f"interval '{settings.s3_sticker_expires_days} days'")
        )
        .order_by(GeneratedImage.created_at.desc())
        .limit(limit + 1)
    )

    if mode == "mine":
        q = q.where(GeneratedImage.on_channel == channel_id)
    elif mode == "with_me":
        q = q.where(GeneratedImage.prompt.ilike(f"%@{login.lower()}%"))
        q = q.where(GeneratedImage.on_channel != channel_id)
    elif mode == "from_me":
        q = q.where(sa.func.lower(GeneratedImage.by_chatter) == login.lower())
        q = q.where(GeneratedImage.on_channel != channel_id)
    else:  # pragma: no cover - defensive
        raise ValueError(f"Unknown stickers mode: {mode!r}")

    if before is not None:
        q = q.where(GeneratedImage.created_at < before)

    return q


def serialize_sticker_rows(rows: Sequence[Any], limit: int) -> dict:
    items = rows[:limit]
    next_cursor = items[-1][0].created_at.isoformat() if len(rows) > limit and items else None
    return {
        "items": [
            {
                "file_id": str(img.file_id),
                "prompt": img.prompt,
                "by_chatter": img.by_chatter,
                "channel_login": channel_login,
                "created_at": img.created_at.isoformat(),
            }
            for img, channel_login in items
        ],
        "next_cursor": next_cursor,
    }
