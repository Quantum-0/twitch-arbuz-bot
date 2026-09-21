"""add steam_id, steam to links and enable_steam_link to settings

Revision ID: 78a98d40f5cf
Revises: dd44d43d91ea
Create Date: 2026-09-21 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "78a98d40f5cf"
down_revision: str | Sequence[str] | None = "dd44d43d91ea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "links",
        sa.Column("steam_id", sa.String(), nullable=True),
    )
    op.add_column(
        "links",
        sa.Column("steam", sa.String(), nullable=True),
    )
    op.add_column(
        "twitch_user_settings",
        sa.Column("enable_steam_link", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("twitch_user_settings", "enable_steam_link")
    op.drop_column("links", "steam")
    op.drop_column("links", "steam_id")
