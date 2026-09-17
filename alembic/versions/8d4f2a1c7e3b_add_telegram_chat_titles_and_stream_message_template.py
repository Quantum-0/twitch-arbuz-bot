"""add telegram chat titles and stream_message_template

Revision ID: 8d4f2a1c7e3b
Revises: 3c9ce04fdc36
Create Date: 2026-09-18 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8d4f2a1c7e3b"
down_revision: str | Sequence[str] | None = "3c9ce04fdc36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("telegram_settings", sa.Column("stream_chat_title", sa.String(), nullable=True))
    op.add_column("telegram_settings", sa.Column("clips_chat_title", sa.String(), nullable=True))
    op.add_column("telegram_settings", sa.Column("stickers_chat_title", sa.String(), nullable=True))
    op.add_column(
        "telegram_settings",
        sa.Column("stream_message_template", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("telegram_settings", "stream_message_template")
    op.drop_column("telegram_settings", "stickers_chat_title")
    op.drop_column("telegram_settings", "clips_chat_title")
    op.drop_column("telegram_settings", "stream_chat_title")
