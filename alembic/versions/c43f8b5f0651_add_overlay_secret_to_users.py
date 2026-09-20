"""add overlay_secret to users

Revision ID: c43f8b5f0651
Revises: 8d4f2a1c7e3b
Create Date: 2026-09-19 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c43f8b5f0651"
down_revision: str | Sequence[str] | None = "8d4f2a1c7e3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "twitch_bot_users",
        sa.Column("overlay_secret", UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("twitch_bot_users", "overlay_secret")
