"""add overlays_last_usage to users

Revision ID: 5f063eb53813
Revises: 492cf52bc513
Create Date: 2026-07-19 07:15:19.112029

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f063eb53813"
down_revision: str | Sequence[str] | None = "492cf52bc513"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("twitch_bot_users", sa.Column("overlays_last_usage", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("twitch_bot_users", "overlays_last_usage")
