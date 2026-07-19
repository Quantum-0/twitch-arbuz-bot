"""add enable_memealerts_link to settings

Revision ID: a1b2c3d4e5f6
Revises: 5f063eb53813
Create Date: 2026-07-19 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "5f063eb53813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "twitch_user_settings",
        sa.Column("enable_memealerts_link", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("twitch_user_settings", "enable_memealerts_link")
