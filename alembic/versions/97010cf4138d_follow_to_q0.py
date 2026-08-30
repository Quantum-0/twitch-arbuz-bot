"""follow to q0

Revision ID: 97010cf4138d
Revises: cd92d500ee31
Create Date: 2026-08-30 14:39:30.436869

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '97010cf4138d'
down_revision: Union[str, Sequence[str], None] = 'cd92d500ee31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('twitch_bot_users', sa.Column('followed_to_admin', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('twitch_bot_users', 'followed_to_admin')
