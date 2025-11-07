"""Add more settings

Revision ID: b19801ec05ae
Revises: e5b36658a27b
Create Date: 2025-09-22 22:55:33.374274

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b19801ec05ae'
down_revision: Union[str, Sequence[str], None] = 'e5b36658a27b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('twitch_user_settings', sa.Column('enable_horny_good', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('twitch_user_settings', sa.Column('enable_horny_bad', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('twitch_user_settings', sa.Column('enable_tail', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('twitch_user_settings', 'enable_horny_bad')
    op.drop_column('twitch_user_settings', 'enable_horny_good')
    op.drop_column('twitch_user_settings', 'enable_tail')
