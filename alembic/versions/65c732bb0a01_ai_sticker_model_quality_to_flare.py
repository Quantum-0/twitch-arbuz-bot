"""ai sticker model quality to flare

Перевод ИИ-стикеров на gpt-image-2.5-flare / gpt-image-2.5-sunburst:
- пользователи с legacy-режимом "quality" (gpt-image-2) переводятся на "flare";
- server_default колонки ai_sticker_model -> 'flare'.

Revision ID: 65c732bb0a01
Revises: 3b543b6a3c35
Create Date: 2026-10-18 17:00:07.201021

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "65c732bb0a01"
down_revision: str | Sequence[str] | None = "3b543b6a3c35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE twitch_user_settings SET ai_sticker_model = 'flare' WHERE ai_sticker_model = 'quality'")
    op.alter_column("twitch_user_settings", "ai_sticker_model", server_default="flare")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE twitch_user_settings SET ai_sticker_model = 'quality' WHERE ai_sticker_model = 'flare'")
    op.alter_column("twitch_user_settings", "ai_sticker_model", server_default="quality")
