"""Add dice command

Revision ID: a6e0792566da
Revises: 52ef83f3fea6
Create Date: 2025-11-17 04:44:16.904498

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a6e0792566da'
down_revision: Union[str, Sequence[str], None] = '52ef83f3fea6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('twitch_user_settings', sa.Column('enable_dice', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('twitch_user_settings', 'enable_dice')
