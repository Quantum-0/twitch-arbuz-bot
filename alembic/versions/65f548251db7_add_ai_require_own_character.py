"""add ai_require_own_character

Revision ID: 65f548251db7
Revises: 697cc264199c
Create Date: 2026-08-01 04:07:27.228597

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "65f548251db7"
down_revision: Union[str, Sequence[str], None] = "697cc264199c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "twitch_user_settings",
        sa.Column("ai_require_own_character", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("twitch_user_settings", "ai_require_own_character")
