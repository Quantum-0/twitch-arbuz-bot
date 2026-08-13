"""add enable_scratch to settings

Revision ID: 1ae68b9b0493
Revises: 0dfd8c9adf27
Create Date: 2026-08-13 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1ae68b9b0493"
down_revision: str | Sequence[str] | None = "0dfd8c9adf27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "twitch_user_settings",
        sa.Column("enable_scratch", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("twitch_user_settings", "enable_scratch")
