"""approved default null

Revision ID: dd44d43d91ea
Revises: aef7a7240053
Create Date: 2026-09-21 15:45:51.727533

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dd44d43d91ea"
down_revision: str | Sequence[str] | None = "aef7a7240053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "character_info",
        "approved",
        server_default=None,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "character_info",
        "approved",
        server_default="true",
    )
